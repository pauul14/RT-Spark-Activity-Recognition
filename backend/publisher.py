"""Local MQTT test publisher. Dataset replay is explicit; no training is performed."""
import argparse
from copy import deepcopy
import importlib
import json
from threading import Event
import uuid

import paho.mqtt.client as mqtt

from .config import PROJECT, Settings
from .mqtt_transport import configure_client
from .validation import AXES, DEMO_METADATA


def fixture_messages(count=8, *, device_id="dataset-demo", boot_id="fixture-boot", mode="demo"):
    if not 4 <= count <= 100:
        raise ValueError("Fixture batch count must be 4..100")
    metadata = dict(DEMO_METADATA)
    if mode == "demo":
        # Reuse the existing loader's reviewed checksum and timestamp interpretation.
        import sys
        ml_path = str(PROJECT / "ml")
        if ml_path not in sys.path:
            sys.path.insert(0, ml_path)
        baseline = importlib.import_module("baseline")
        segments, _ = baseline.load_dataset(PROJECT / "ml" / "data" / "dataset.csv")
        selected = []
        for segment in segments:
            selected = []
            for row in segment:
                if selected and (not 100_000_000 <= row["t"] - selected[-1]["t"] <= 300_000_000
                                 or row["y"] != selected[-1]["y"]):
                    selected = []
                selected.append(row)
                if len(selected) == count * 5:
                    break
            if len(selected) == count * 5:
                break
        if len(selected) != count * 5:
            raise ValueError("No suitable consecutive dataset fixture found")
        origin = selected[0]["t"]
        samples = [{"t_ms": (row["t"] - origin) // 1_000_000,
                    **dict(zip(AXES, row["values"].tolist()))} for row in selected]
        prefix = "bca182/demo/v1"
    elif mode == "hardware":
        # Explicit deterministic wire-validation fixture, not real sensor measurements.
        metadata = {"sensor_model": "unverified", "frame_id": "sensor_native_unmapped",
                    "calibration_id": None, "acceleration_unit": "g", "gyro_unit": "rad/s",
                    "gravity_handling": "included"}
        samples = [{"t_ms": i * 200, **dict.fromkeys(AXES, 0.0), "acceleration_z": 1.0}
                   for i in range(count * 5)]
        prefix = "bca182/v1"
    else:
        raise ValueError("Unknown fixture mode")
    topic = f"{prefix}/devices/{device_id}/motion"
    messages = [{"schema_version": 1, "device_id": device_id, "boot_id": boot_id, "seq": i,
                 "sample_hz": 5.0, "timestamp_unit": "ms", "timestamp_source": "device_monotonic",
                 **metadata, "samples": samples[i * 5:(i + 1) * 5]} for i in range(count)]
    return topic, messages


def scenario_messages(messages, scenario):
    messages = deepcopy(messages)
    if scenario == "duplicate":
        messages.insert(4, deepcopy(messages[3]))
    elif scenario == "missing":
        del messages[2]
    elif scenario == "invalid":
        del messages[2]["samples"][0]["gyro_z"]
    elif scenario == "timestamps":
        messages[2]["samples"][0]["t_ms"] = messages[2]["samples"][1]["t_ms"]
    elif scenario == "out-of-order":
        messages[1], messages[2] = messages[2], messages[1]
    return messages


def publish(settings, topic, messages, *, scenario="valid", interval=1.0):
    ready, disconnected = Event(), Event()
    def connected(c, userdata, flags, reason_code, properties):
        if reason_code == 0:
            ready.set()
    def lost(c, userdata, flags, reason_code, properties):
        ready.clear()
        disconnected.set()
    client_id = "bca182-publisher-" + uuid.uuid4().hex
    def start_client():
        connection = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id,
                                 clean_session=True, protocol=mqtt.MQTTv311)
        configure_client(connection, settings)
        connection.on_connect, connection.on_disconnect = connected, lost
        connection.connect(settings.mqtt_host, settings.mqtt_port, keepalive=30)
        connection.loop_start()
        return connection
    client = start_client()
    try:
        if not ready.wait(5):
            raise RuntimeError("MQTT connection did not become ready")
        outgoing = scenario_messages(messages, scenario)
        for index, message in enumerate(outgoing):
            if scenario == "reconnect" and index == 2:
                disconnected.clear()
                client.disconnect()
                if not disconnected.wait(5):
                    raise RuntimeError("Publisher disconnect timed out")
                # Paho 2.1.0 loop_start replaces an existing internal socket pair.
                # A fresh client avoids that leak using only public APIs.
                client.loop_stop()
                client = start_client()
                if not ready.wait(5):
                    raise RuntimeError("Publisher reconnect timed out")
            info = client.publish(topic, json.dumps(message, allow_nan=False), qos=1, retain=False)
            info.wait_for_publish(timeout=5)
            if not info.is_published():
                raise RuntimeError("MQTT publish was not acknowledged")
            print(f"published seq={message['seq']} source={'dataset' if '/demo/' in topic else 'deterministic_hardware_schema_fixture'}")
            # Duplicate/stale fixture deliveries form a retry burst; they must
            # not postpone fresh samples by an additional nominal interval.
            if interval and index < len(outgoing) - 1 and outgoing[index + 1]["seq"] > message["seq"]:
                Event().wait(interval)
    finally:
        client.disconnect()
        client.loop_stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["demo", "hardware"], default="demo")
    parser.add_argument("--device")
    parser.add_argument("--batches", type=int, default=8)
    parser.add_argument("--scenario", choices=["valid", "duplicate", "missing", "invalid", "timestamps",
                                               "out-of-order", "reconnect"], default="valid")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not 0 <= args.interval <= 10:
        parser.error("interval must be 0..10 seconds")
    device_id = args.device or ("dataset-demo" if args.mode == "demo" else "hardware-schema-fixture")
    topic, messages = fixture_messages(args.batches, device_id=device_id, boot_id=uuid.uuid4().hex, mode=args.mode)
    if args.dry_run:
        for message in scenario_messages(messages, args.scenario):
            print(json.dumps({"topic": topic, "payload": message}))
    else:
        publish(Settings.from_env(), topic, messages, scenario=args.scenario, interval=args.interval)


if __name__ == "__main__":
    main()
