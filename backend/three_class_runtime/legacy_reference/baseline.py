"""Windowed walking/running baseline. No firmware or cloud dependencies."""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import date
import hashlib
from importlib.metadata import version
import json
import math
from pathlib import Path
import platform
import warnings

import joblib
import numpy as np
from sklearn.dummy import DummyClassifier
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from download_dataset import EXPECTED_SHA256

ROOT = Path(__file__).resolve().parent
AXES = ("acceleration_x", "acceleration_y", "acceleration_z", "gyro_x", "gyro_y", "gyro_z")
CHANNELS = AXES + ("acceleration_magnitude", "gyro_magnitude")
STATS = ("mean", "std", "min", "max", "rms", "mean_abs_difference", "lag1_correlation")
FEATURE_NAMES = [f"{channel}.{stat}" for stat in STATS for channel in CHANNELS]
CLASSES = {0: "walking", 1: "running"}
CONFIG = {
    "schema_version": 1,
    "input_contract": "kaggle_vmalyi_v2_native",
    "sensor_axes": list(AXES),
    "sensor_units": "unverified; retain the original CSV numeric scale",
    "gravity_handling": "unverified; no gravity removal or unit conversion applied",
    "window_seconds": 4.0,
    "resample_hz": 5.0,
    "resampled_points": 20,
    "max_gap_seconds": 0.6,
    "min_raw_samples": 16,
    "median_interval_bounds_seconds": [0.12, 0.28],
    "interpolation": "linear, 0.0 to 3.8 seconds inclusive; no extrapolation",
    "window_policy": "disjoint raw rows; next window starts at next unused sample",
    "stream_boundaries": "username, date, wrist, or timestamp gap >0.6 seconds",
    "mixed_activity_windows": "excluded; no label-majority assignment",
    "feature_names": FEATURE_NAMES,
    "feature_std_ddof": 0,
    "split": "chronological dates; last ceil(20% of dates) held out before fitting",
    "random_seed": 42,
}
# Inference admission policy only. CONFIG and saved artifact metadata retain the
# original training/evaluation policy so historical windows remain reproducible.
PREDICTION_TIMING = {
    "version": "approximately_4s_v1",
    "min_span_seconds": 3.7,
    "max_span_seconds": 4.1,
    "interval_bounds_seconds": (0.1, 0.3),
    "comparison_epsilon_seconds": 1e-9,
}


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def parse_timestamp(day, time):
    """Interpret the unpadded fourth field as integer nanoseconds; timezone is unknown."""
    y, m, d = map(int, day.split("-"))
    h, minute, second, fraction = time.split(":")
    h, minute, second = int(h), int(minute), int(second)
    if not (0 <= h < 24 and 0 <= minute < 60 and 0 <= second < 60):
        raise ValueError("Invalid time of day")
    if not fraction.isdigit() or not 1 <= len(fraction) <= 9:
        raise ValueError("Expected an integer nanosecond field with at most nine digits")
    calendar_day = date(y, m, d)
    seconds = (calendar_day - date(1970, 1, 1)).days * 86400 + h * 3600 + minute * 60 + second
    return seconds * 1_000_000_000 + int(fraction), calendar_day.isoformat()


def load_dataset(path):
    path = Path(path)
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    if checksum != EXPECTED_SHA256:
        raise ValueError("CSV checksum differs from reviewed Kaggle version 2; review changes before training")
    rows = []
    seen = set()
    with path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        required = {"date", "time", "username", "wrist", "activity", *AXES}
        if set(reader.fieldnames or []) != required:
            raise ValueError(f"Unexpected CSV schema: {reader.fieldnames}")
        for row_id, row in enumerate(reader):
            try:
                if any(value is None or not value.strip() for value in row.values()):
                    raise ValueError("Missing value")
                timestamp, day = parse_timestamp(row["date"], row["time"])
                if row["activity"] not in {"0", "1"} or row["wrist"] not in {"0", "1"}:
                    raise ValueError("Invalid activity or wrist label")
                values = np.array([float(row[axis]) for axis in AXES])
                if not np.isfinite(values).all():
                    raise ValueError("Non-finite sensor value")
                key = (row["username"], day, int(row["wrist"]), timestamp)
                if key in seen:
                    raise ValueError("Duplicate timestamp in the same subject/date/wrist stream")
                seen.add(key)
                rows.append({"id": row_id, "t": timestamp, "day": day, "subject": row["username"],
                             "wrist": int(row["wrist"]), "y": int(row["activity"]), "values": values})
            except (ValueError, TypeError) as exc:
                raise ValueError(f"CSV line {row_id + 2}: {exc}") from exc
    if not rows or set(row["y"] for row in rows) != {0, 1}:
        raise ValueError("Both activity classes are required")
    streams = defaultdict(list)
    for row in rows:
        streams[(row["subject"], row["day"], row["wrist"])].append(row)
    intervals, segments = [], []
    for key in sorted(streams):
        stream = sorted(streams[key], key=lambda row: row["t"])
        run = []
        for row in stream:
            if run:
                interval = (row["t"] - run[-1]["t"]) / 1e9
                intervals.append(interval)
                if interval > CONFIG["max_gap_seconds"]:
                    segments.append(run)
                    run = []
            run.append(row)
        if run:
            segments.append(run)
    short_intervals = np.array([x for x in intervals if x <= CONFIG["max_gap_seconds"]])
    if not len(short_intervals):
        raise ValueError("No usable temporal sequences")
    measurements = np.stack([row["values"] for row in rows])
    audit = {
        "csv_sha256": checksum, "rows": len(rows), "missing_or_nonfinite_values": 0,
        "duplicate_stream_timestamps": 0, "columns": list(reader.fieldnames),
        "class_counts": {CLASSES[k]: v for k, v in sorted(Counter(row["y"] for row in rows).items())},
        "subjects": sorted({row["subject"] for row in rows}),
        "dates": sorted({row["day"] for row in rows}),
        "wrist_counts": dict(Counter(str(row["wrist"]) for row in rows)),
        "streams": len(streams), "inferred_segments": len(segments),
        "explicit_session_id": False,
        "timestamp_timezone": "unknown; parsed as local calendar values without UTC conversion",
        "fractional_seconds_interpretation": "unpadded integer nanosecond field; inferred from cadence, not owner-documented",
        "source_order_timestamp_reversals": sum(b["t"] < a["t"] for a, b in zip(rows, rows[1:])),
        "within_segment_interval_seconds_quantiles": dict(zip(
            ["min", "p05", "median", "p95", "max"],
            np.quantile(short_intervals, [0, .05, .5, .95, 1]).tolist())),
        "median_based_hz": float(1 / np.median(short_intervals)),
        "gaps_over_threshold": sum(x > CONFIG["max_gap_seconds"] for x in intervals),
        "sensor_ranges": {axis: {"min": float(measurements[:, i].min()),
                                 "max": float(measurements[:, i].max())} for i, axis in enumerate(AXES)},
        "units": CONFIG["sensor_units"], "gravity_handling": CONFIG["gravity_handling"],
    }
    return segments, audit


def resample_window(times, sensors, config=CONFIG, *, prediction_tolerance=False):
    """Keep the training grid; optionally allow bounded inference timing jitter.

    Short inference windows hold the last reading for at most 0.1 seconds.
    Existing training inputs retain their original strict coverage rules.
    """
    times = np.asarray(times, dtype=float)
    sensors = np.asarray(sensors, dtype=float)
    if times.ndim != 1 or sensors.shape != (len(times), len(AXES)):
        raise ValueError("Expected one timestamp and six sensor axes per sample")
    if len(times) < config["min_raw_samples"] or not np.isfinite(times).all() or not np.isfinite(sensors).all():
        raise ValueError("Too few samples or non-finite input")
    times = times - times[0]
    intervals = np.diff(times)
    if np.any(intervals <= 0) or np.any(intervals > config["max_gap_seconds"]):
        raise ValueError("Timestamps must increase strictly with no gap greater than 0.6 seconds")
    lo, hi = config["median_interval_bounds_seconds"]
    if not lo <= np.median(intervals) <= hi:
        raise ValueError("Input cadence differs from the baseline's supported range")
    target = np.arange(config["resampled_points"], dtype=float) / config["resample_hz"]
    if prediction_tolerance:
        eps = PREDICTION_TIMING["comparison_epsilon_seconds"]
        if (times[-1] < PREDICTION_TIMING["min_span_seconds"] - eps
                or times[-1] > PREDICTION_TIMING["max_span_seconds"] + eps):
            raise ValueError("Prediction window must span 3.7 to 4.1 seconds inclusive")
        interval_lo, interval_hi = PREDICTION_TIMING["interval_bounds_seconds"]
        if np.any(intervals < interval_lo - eps) or np.any(intervals > interval_hi + eps):
            raise ValueError("Prediction intervals must stay between 0.1 and 0.3 seconds")
    elif times[-1] < target[-1] or times[-1] >= config["window_seconds"]:
        raise ValueError("Window must cover at least 3.8 seconds and less than 4.0 seconds")
    # np.interp keeps the original linear interpolation for supported times and
    # holds the endpoint for the bounded shortfall allowed only at prediction.
    return np.stack([np.interp(target, times, sensors[:, axis]) for axis in range(len(AXES))], axis=1)


def extract_features(samples):
    samples = np.asarray(samples, dtype=float)
    if samples.shape != (20, 6) or not np.isfinite(samples).all():
        raise ValueError("Feature extraction requires 20 finite, resampled six-axis observations")
    data = np.column_stack((samples, np.linalg.norm(samples[:, :3], axis=1),
                            np.linalg.norm(samples[:, 3:], axis=1)))
    previous = data[:-1] - data[:-1].mean(axis=0)
    following = data[1:] - data[1:].mean(axis=0)
    denominator = np.sqrt((previous ** 2).sum(axis=0) * (following ** 2).sum(axis=0))
    lag1 = np.divide((previous * following).sum(axis=0), denominator,
                     out=np.zeros(data.shape[1]), where=denominator > 1e-12)
    return np.concatenate((data.mean(axis=0), data.std(axis=0), data.min(axis=0), data.max(axis=0),
                           np.sqrt((data ** 2).mean(axis=0)), np.abs(np.diff(data, axis=0)).mean(axis=0), lag1))


def make_windows(segments):
    windows, discarded = [], Counter()
    for segment_id, segment in enumerate(segments):
        stamps = np.array([row["t"] for row in segment], dtype=np.int64)
        start = 0
        while start < len(segment):
            end = int(np.searchsorted(stamps, stamps[start] + 4_000_000_000, side="left"))
            raw = segment[start:end]
            start = end  # Consume these rows even when the window is rejected.
            if len({row["y"] for row in raw}) != 1:
                discarded["mixed_activity"] += 1
                continue
            times = (stamps[end - len(raw):end] - raw[0]["t"]).astype(float) / 1e9
            sensors = np.stack([row["values"] for row in raw])
            try:
                features = extract_features(resample_window(times, sensors))
            except ValueError as exc:
                discarded[str(exc)] += 1
                continue
            windows.append({"window_id": len(windows), "segment": segment_id, "day": raw[0]["day"],
                            "subject": raw[0]["subject"], "wrist": raw[0]["wrist"], "y": raw[0]["y"],
                            "start_ns": raw[0]["t"], "end_ns": raw[-1]["t"],
                            "row_ids": [row["id"] for row in raw], "times": times, "sensors": sensors,
                            "features": features})
    return windows, dict(discarded)


def partition(windows, all_dates):
    dates = sorted(set(all_dates))
    if len(dates) < 3:
        raise ValueError("At least three recording dates are required for this split")
    held_out = set(dates[-math.ceil(.2 * len(dates)):])
    train = [window for window in windows if window["day"] not in held_out]
    test = [window for window in windows if window["day"] in held_out]
    for name, split in (("train", train), ("test", test)):
        if set(window["y"] for window in split) != {0, 1}:
            raise ValueError(f"Both classes must occur in {name}; do not silently change the split")
    train_ids = {row for window in train for row in window["row_ids"]}
    test_ids = {row for window in test for row in window["row_ids"]}
    if train_ids & test_ids:
        raise AssertionError("Source rows overlap between train and test")
    if sum(len(w["row_ids"]) for w in windows) != len(train_ids | test_ids):
        raise AssertionError("Source rows overlap between windows")
    return train, test, {
        "train_dates": [day for day in dates if day not in held_out], "test_dates": sorted(held_out),
        "train_windows": len(train), "test_windows": len(test),
        "shared_source_rows": 0, "shared_dates": 0,
        "train_class_windows": dict(Counter(CLASSES[w["y"]] for w in train)),
        "test_class_windows": dict(Counter(CLASSES[w["y"]] for w in test)),
    }


def evaluate(labels, predictions):
    return {"accuracy": float(accuracy_score(labels, predictions)),
            "classification_report": classification_report(labels, predictions, labels=[0, 1],
                target_names=[CLASSES[0], CLASSES[1]], output_dict=True, zero_division=0),
            "confusion_matrix": confusion_matrix(labels, predictions, labels=[0, 1]).tolist(),
            "confusion_matrix_order": ["walking", "running"],
            "confusion_matrix_axes": "rows=true, columns=predicted"}


class Predictor:
    """Reusable local interface; accepts dataset-compatible sensor windows only."""
    def __init__(self, model_path=ROOT / "artifacts" / "model.joblib"):
        # Load only this project's trusted artifact: joblib is a pickle-based format.
        bundle = joblib.load(model_path)
        self.model = bundle["pipeline"]
        self.config = bundle["preprocessing"]
        if self.config != CONFIG:
            raise ValueError("Model preprocessing differs from this implementation")

    def predict_window(self, payload):
        if not isinstance(payload, dict) or payload.get("input_contract") != self.config["input_contract"]:
            raise ValueError("Explicit dataset-native input_contract is required; raw STM32 compatibility is unverified")
        samples = payload.get("samples")
        if not isinstance(samples, list) or not samples:
            raise ValueError("samples must be a nonempty list")
        try:
            times = [sample["t_seconds"] for sample in samples]
            sensors = [[sample[axis] for axis in AXES] for sample in samples]
            features = extract_features(resample_window(
                times, sensors, self.config, prediction_tolerance=True))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid sensor window: {exc}") from exc
        probabilities = self.model.predict_proba(features.reshape(1, -1))[0]
        label = int(self.model.classes_[int(np.argmax(probabilities))])
        return {"activity": CLASSES[label], "label": label,
                "probabilities": {CLASSES[int(k)]: float(v) for k, v in zip(self.model.classes_, probabilities)},
                "probability_note": "model scores; calibration and out-of-distribution detection not evaluated",
                "input_contract": self.config["input_contract"], "hardware_validated": False}


def train(csv_path, output):
    output = Path(output)
    segments, audit = load_dataset(csv_path)
    windows, discarded = make_windows(segments)
    training, testing, split = partition(windows, audit["dates"])
    x_train = np.stack([w["features"] for w in training])
    x_test = np.stack([w["features"] for w in testing])
    y_train = np.array([w["y"] for w in training])
    y_test = np.array([w["y"] for w in testing])
    pipeline = make_pipeline(StandardScaler(), LogisticRegression(
        C=1.0, class_weight="balanced", solver="lbfgs", max_iter=2000, random_state=42))
    # A single predeclared model; no tuning against held-out dates.
    with warnings.catch_warnings(), threadpool_limits(limits=1):
        warnings.simplefilter("error", ConvergenceWarning)
        pipeline.fit(x_train, y_train)
        predicted = pipeline.predict(x_test)
        probabilities = pipeline.predict_proba(x_test)
    dummy = DummyClassifier(strategy="most_frequent").fit(x_train, y_train)
    report = {"model": "StandardScaler + balanced L2 logistic regression, C=1",
              "evaluation_unit": "accepted homogeneous four-second sensor window",
              "feature_count": len(FEATURE_NAMES), "split": split,
              "discarded_window_candidates": discarded,
              "accepted_windows": len(windows),
              "accepted_source_rows": sum(len(w["row_ids"]) for w in windows),
              "baseline": evaluate(y_test, predicted),
              "majority_class_reference": evaluate(y_test, dummy.predict(x_test)),
              "test_by_date": {}, "test_by_wrist": {},
              "limitations": ["one subject only; no unseen-person evaluation",
                  "physical sensor, units, gravity treatment and orientation compatibility unverified",
                  "calendar-day holdout; explicit recording/session IDs unavailable",
                  "timestamps alone cannot reveal every hidden collection boundary",
                  "short, gapped and mixed-activity windows excluded; no transition evaluation",
                  "no calibrated confidence or unknown-activity rejection",
                  "same user's nearby days are not independent people; no general-population claim"],
              "versions": {name: version(name) for name in
                           ("numpy", "scipy", "scikit-learn", "joblib", "threadpoolctl")},
              "python": platform.python_version(), "csv_sha256": audit["csv_sha256"]}
    for field, target in (("day", "test_by_date"), ("wrist", "test_by_wrist")):
        for value in sorted({w[field] for w in testing}):
            mask = np.array([w[field] == value for w in testing])
            report[target][str(value)] = {"windows": int(mask.sum()), **evaluate(y_test[mask], predicted[mask])}
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "dataset_audit.json", audit)
    write_json(output / "preprocessing.json", CONFIG)
    write_json(output / "metrics.json", report)
    joblib.dump({"pipeline": pipeline, "preprocessing": CONFIG, "csv_sha256": audit["csv_sha256"]},
                output / "model.joblib", compress=3)
    scaler, logistic = pipeline.steps[0][1], pipeline.steps[1][1]
    write_json(output / "model_parameters.json", {
        "feature_names": FEATURE_NAMES, "classes": logistic.classes_.tolist(),
        "scaler_mean": scaler.mean_.tolist(), "scaler_scale": scaler.scale_.tolist(),
        "coefficients": logistic.coef_[0].tolist(), "intercept": float(logistic.intercept_[0]),
        "formula": "p(running) = sigmoid(intercept + sum(coef * ((feature - mean) / scale)))"})
    with (output / "windows.csv").open("w", newline="", encoding="utf-8") as destination:
        fields = ["window_id", "split", "day", "subject", "wrist", "segment", "y", "start_ns", "end_ns", "source_row_ids"]
        writer = csv.DictWriter(destination, fieldnames=fields)
        writer.writeheader()
        test_dates = set(split["test_dates"])
        for window in windows:
            record = {key: window[key] for key in fields if key in window}
            record.update(split="test" if window["day"] in test_dates else "train",
                          source_row_ids=json.dumps(window["row_ids"]))
            writer.writerow(record)
    with (output / "test_predictions.csv").open("w", newline="", encoding="utf-8") as destination:
        writer = csv.writer(destination)
        writer.writerow(["window_id", "day", "wrist", "true_label", "predicted_label", "p_walking", "p_running"])
        for window, label, probability in zip(testing, predicted, probabilities):
            writer.writerow([window["window_id"], window["day"], window["wrist"], window["y"], int(label), *probability])
    sample = testing[0]
    payload = {"input_contract": CONFIG["input_contract"], "samples": [
        {"t_seconds": float(t), **dict(zip(AXES, values.tolist()))}
        for t, values in zip(sample["times"], sample["sensors"])]}
    write_json(output / "example_window.json", payload)
    roundtrip = Predictor(output / "model.joblib").predict_window(payload)
    if roundtrip["label"] != int(predicted[0]) or not np.allclose(
            list(roundtrip["probabilities"].values()), probabilities[0], rtol=0, atol=1e-12):
        raise AssertionError("Saved-model predictions differ from evaluation")
    write_json(output / "example_prediction.json", roundtrip)
    print(json.dumps({"split": split, "baseline": report["baseline"],
                      "majority_accuracy": report["majority_class_reference"]["accuracy"]}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    training = commands.add_parser("train")
    training.add_argument("--data", type=Path, default=ROOT / "data" / "dataset.csv")
    training.add_argument("--output", type=Path, default=ROOT / "artifacts")
    prediction = commands.add_parser("predict")
    prediction.add_argument("--model", type=Path, default=ROOT / "artifacts" / "model.joblib")
    prediction.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "train":
        train(args.data, args.output)
    else:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        print(json.dumps(Predictor(args.model).predict_window(payload), indent=2))


if __name__ == "__main__":
    main()
