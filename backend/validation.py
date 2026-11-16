"""Strict wire validation, with an explicit opt-in dataset replay namespace."""
from dataclasses import dataclass
import hashlib
import json
import math
import re

AXES = ("acceleration_x", "acceleration_y", "acceleration_z", "gyro_x", "gyro_y", "gyro_z")
MAX_INTEGER = 2**53 - 1
MAX_BYTES = 16384
TOPIC = re.compile(r"^bca182/(?P<demo>demo/)?v1/devices/(?P<device>[A-Za-z0-9_-]{1,64})/motion$")
IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
FIELDS = {"schema_version", "device_id", "boot_id", "seq", "sample_hz", "timestamp_unit",
          "timestamp_source", "sensor_model", "frame_id", "calibration_id", "acceleration_unit",
          "gyro_unit", "gravity_handling", "samples"}
DEMO_METADATA = {"sensor_model": "kaggle-v2-replay", "frame_id": "kaggle_vmalyi_v2_native",
                 "calibration_id": "dataset-v2-native", "acceleration_unit": "dataset_native_unverified",
                 "gyro_unit": "dataset_native_unverified", "gravity_handling": "unverified"}


class InvalidPayload(ValueError):
    """A rejected MQTT message; error text must not contain raw payload or secrets."""


def number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def integer(value):
    return type(value) is int and 0 <= value <= MAX_INTEGER


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidPayload("duplicate_json_key")
        result[key] = value
    return result


@dataclass(frozen=True)
class SensorMessage:
    device_id: str
    boot_id: str
    seq: int
    samples: list[dict]
    mode: str
    digest: str
    fingerprint: str


def validate(topic, payload, *, allow_demo=False, retained=False):
    match = TOPIC.fullmatch(topic)
    if not match:
        raise InvalidPayload("invalid_topic")
    if retained:
        raise InvalidPayload("retained_messages_forbidden")
    if not isinstance(payload, bytes) or len(payload) > MAX_BYTES:
        raise InvalidPayload("invalid_payload_size_or_type")
    try:
        def reject_constant(_):
            raise InvalidPayload("nonfinite_json_constant")
        body = json.loads(payload.decode("utf-8"), object_pairs_hook=unique_object,
                          parse_constant=reject_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise InvalidPayload("invalid_json") from exc
    if not isinstance(body, dict) or set(body) != FIELDS:
        raise InvalidPayload("invalid_message_fields")
    if body["device_id"] != match["device"]:
        raise InvalidPayload("topic_device_mismatch")
    if not isinstance(body["boot_id"], str) or not IDENTIFIER.fullmatch(body["boot_id"]):
        raise InvalidPayload("invalid_boot_id")
    if not integer(body["seq"]) or type(body["schema_version"]) is not int or body["schema_version"] != 1:
        raise InvalidPayload("invalid_sequence_or_schema")
    if (not number(body["sample_hz"]) or body["sample_hz"] != 5
            or body["timestamp_unit"] != "ms" or body["timestamp_source"] != "device_monotonic"):
        raise InvalidPayload("invalid_sampling_or_clock")
    demo = bool(match["demo"])
    if demo:
        if not allow_demo:
            raise InvalidPayload("dataset_demo_disabled")
        if any(body[key] != value for key, value in DEMO_METADATA.items()):
            raise InvalidPayload("invalid_dataset_demo_metadata")
    else:
        if (body["acceleration_unit"] != "g" or body["gyro_unit"] != "rad/s"
                or body["gravity_handling"] != "included"):
            raise InvalidPayload("invalid_hardware_units")
        for key in ("sensor_model", "frame_id"):
            if not isinstance(body[key], str) or not 1 <= len(body[key]) <= 128:
                raise InvalidPayload("invalid_sensor_metadata")
        if body["calibration_id"] is not None and (
                not isinstance(body["calibration_id"], str) or not 1 <= len(body["calibration_id"]) <= 128):
            raise InvalidPayload("invalid_calibration_id")
    samples = body["samples"]
    if not isinstance(samples, list) or len(samples) != 5:
        raise InvalidPayload("exactly_five_samples_required")
    last = None
    for sample in samples:
        if not isinstance(sample, dict) or set(sample) != {"t_ms", *AXES}:
            raise InvalidPayload("invalid_sample_fields")
        if not integer(sample["t_ms"]) or any(not number(sample[axis]) for axis in AXES):
            raise InvalidPayload("invalid_sample_values")
        if last is not None and not 100 <= sample["t_ms"] - last <= 300:
            raise InvalidPayload("invalid_sample_order_or_interval")
        last = sample["t_ms"]
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    fingerprint = json.dumps({key: body[key] for key in sorted(FIELDS - {
        "device_id", "boot_id", "seq", "samples"})}, sort_keys=True)
    return SensorMessage(body["device_id"], body["boot_id"], body["seq"], samples,
                         "dataset_demo" if demo else "hardware_unvalidated",
                         hashlib.sha256(encoded).hexdigest(), fingerprint)
