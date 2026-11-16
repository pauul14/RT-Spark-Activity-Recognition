"""Owned, loopback-only live process check; leaves logs/SQLite but stops its services."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
from threading import Condition, Thread
import time
import uuid

import httpx

from .config import PROJECT, ROOT, Settings
from .inference import Model
from .publisher import fixture_messages, publish


class Process:
    def __init__(self, command, log, env):
        self.lines, self.changed = [], Condition()
        self.log = log
        self.process = subprocess.Popen(command, cwd=PROJECT, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.reader = Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        with self.log.open("w", encoding="utf-8") as output:
            for line in self.process.stdout:
                output.write(line)
                output.flush()
                with self.changed:
                    self.lines.append(line.rstrip())
                    self.changed.notify_all()
        self.process.stdout.close()
        with self.changed:
            self.changed.notify_all()

    def wait_log(self, predicate, timeout=20):
        deadline = time.monotonic() + timeout
        with self.changed:
            while not predicate(self.lines):
                if self.process.poll() is not None:
                    raise RuntimeError(f"Process exited; inspect {self.log}")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"Readiness timeout; inspect {self.log}")
                self.changed.wait(remaining)

    def wait(self, timeout=40):
        result = self.process.wait(timeout)
        self.reader.join(5)
        if result:
            raise RuntimeError(f"Process returned {result}; inspect {self.log}")

    def stop(self, signal_tool=None):
        if self.process.poll() is None:
            if signal_tool is not None:
                subprocess.run([str(signal_tool), "-p", str(self.process.pid), "shutdown"], check=True,
                    capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            else:
                self.process.terminate()
            try:
                self.process.wait(5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(5)
        self.reader.join(5)


def free_port(preferred):
    with socket.socket() as candidate:
        try:
            candidate.bind(("127.0.0.1", preferred))
        except OSError:
            candidate.bind(("127.0.0.1", 0))
        return candidate.getsockname()[1]


def run(broker_path):
    broker_path = broker_path.resolve()
    signal_tool = broker_path.with_name("mosquitto_signal.exe")
    if not broker_path.is_file() or not signal_tool.is_file():
        raise ValueError("Provide an existing official Mosquitto 2.1 Windows executable and signal utility")
    run_id = "live-2bl-" + uuid.uuid4().hex[:10]
    folder = ROOT / "runtime" / run_id
    folder.mkdir(parents=True)
    db_path = folder / "activities.sqlite3"
    mqtt_port, api_port = free_port(1883), free_port(8000)
    conf = ROOT / "mosquitto.conf"
    if mqtt_port != 1883:
        conf = folder / "mosquitto.conf"
        conf.write_text(f"listener {mqtt_port} 127.0.0.1\nallow_anonymous true\npersistence false\n")
    # Inherit no custom credentials or live-test switches for this owned anonymous broker.
    env = dict(os.environ)
    for key in ("MQTT_USERNAME", "MQTT_PASSWORD", "MQTT_CA_FILE", "RUN_LIVE_MQTT", "API_TOKEN", "MODEL_PATH"):
        env.pop(key, None)
    env.update(APP_ENV="local", MQTT_TLS="false", ALLOW_DATASET_DEMO="true", DATABASE_PATH=str(db_path), MQTT_HOST="127.0.0.1",
               MQTT_PORT=str(mqtt_port), API_PORT=str(api_port), MQTT_CLIENT_ID=run_id,
               PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
    settings = Settings(database_path=db_path, mqtt_port=mqtt_port, api_port=api_port,
                        allow_dataset_demo=True, mqtt_client_id=run_id)
    model = Model(settings.model_path)
    _, source = fixture_messages()
    report = {"run_id": run_id, "mqtt_address": f"127.0.0.1:{mqtt_port}",
              "http_address": f"127.0.0.1:{api_port}", "cases": {}, "hardware_validated": False,
              "model_sha256": hashlib.sha256(settings.model_path.read_bytes()).hexdigest()}
    broker = worker = api = None
    try:
        broker = Process([str(broker_path), "-c", str(conf), "-v"], folder / "broker.log", env)
        broker.wait_log(lambda lines: any("running" in line for line in lines))
        worker = Process([sys.executable, "-B", "-m", "backend.mqtt_worker"], folder / "worker.log", env)
        worker.wait_log(lambda lines: "mqtt_subscription_ready" in "\n".join(lines))
        api = Process([sys.executable, "-B", "-m", "backend.api"], folder / "api.log", env)
        api.wait_log(lambda lines: any("Uvicorn running on" in line for line in lines))
        print(f"LIVE broker/worker/API ready; evidence: {folder.relative_to(PROJECT)}", flush=True)
        with httpx.Client(base_url=f"http://127.0.0.1:{api_port}", trust_env=False, timeout=5) as http:
            health = http.get("/health")
            assert health.status_code == 200 and health.json()["database"] == "ok"
            report["health"] = health.json()
            expected_counts = {"valid": 5, "duplicate": 5, "missing": 2, "invalid": 2,
                               "timestamps": 2, "out-of-order": 3, "reconnect": 5}
            for scenario, count in expected_counts.items():
                device = "live-" + scenario
                publisher = Process([sys.executable, "-B", "-m", "backend.publisher", "--device", device,
                    "--scenario", scenario, "--interval", "1"], folder / f"publisher-{scenario}.log", env)
                try:
                    publisher.wait()
                finally:
                    publisher.stop()
                worker.wait_log(lambda lines, device=device: any(
                    f"device={device} seq=7" in line for line in lines))
                response = http.get("/api/activities/history", params={"device_id": device, "limit": 100})
                assert response.status_code == 200
                records = response.json()["items"]
                assert len(records) == count, (scenario, len(records), count)
                assert all(r["inference_mode"] == "dataset_demo" and r["hardware_validated"] is False for r in records)
                assert len({(r["boot_id"], r["device_timestamp_ms"], r["model_id"]) for r in records}) == count
                latest = http.get("/api/activities/latest", params={"device_id": device})
                assert latest.status_code == 200 and latest.json() == records[0]
                if scenario == "valid":
                    expected = model.predict([s for body in source[4:8] for s in body["samples"]])
                    assert records[0]["activity"] == expected["activity"]
                    assert abs(records[0]["running_probability"] - expected["probabilities"]["running"]) < 1e-12
                if scenario in {"missing", "invalid", "timestamps"}:
                    assert records[-1]["sequence_end"] == 6
                if scenario == "duplicate":
                    assert any("status=duplicate" in line and f"device={device}" in line for line in worker.lines)
                report["cases"][scenario] = {"predictions": count, "latest": records[0]}
                print(f"PASS live {scenario}: {count} predictions; HTTP latest/history match", flush=True)

            # Exercise the actual worker's automatic reconnect after a controlled broker shutdown.
            device = "live-broker-recovery"
            topic = f"bca182/demo/v1/devices/{device}/motion"
            bodies = deepcopy(source)
            for body in bodies:
                body.update(device_id=device, boot_id=run_id)
            publish(settings, topic, bodies[:3], interval=1)
            worker.wait_log(lambda lines: any(f"device={device} seq=2" in line for line in lines))
            assert http.get("/api/activities/history", params={"device_id": device}).json()["items"] == []
            ready_count = sum("mqtt_subscription_ready" in line for line in worker.lines)
            disconnect_count = sum("mqtt_disconnected" in line for line in worker.lines)
            broker.stop(signal_tool)
            worker.wait_log(lambda lines: sum("mqtt_disconnected" in line for line in lines) > disconnect_count)
            broker = Process([str(broker_path), "-c", str(conf), "-v"], folder / "broker-restarted.log", env)
            broker.wait_log(lambda lines: any("running" in line for line in lines))
            worker.wait_log(lambda lines: sum("mqtt_subscription_ready" in line for line in lines) > ready_count)
            publish(settings, topic, bodies[2:7], interval=1)
            worker.wait_log(lambda lines: any(f"device={device} seq=6" in line for line in lines))
            recovered = http.get("/api/activities/history", params={"device_id": device}).json()["items"]
            assert len(recovered) == 1 and recovered[0]["sequence_end"] == 6
            assert any("status=duplicate" in line and f"device={device} seq=2" in line for line in worker.lines)
            report["cases"]["broker_restart_recovery"] = {"predictions": 1, "latest": recovered[0]}
            print("PASS live broker restart: automatic worker reconnect/resubscribe, redelivery ignored, fresh warm-up", flush=True)
            assert "reason=invalid_sample_fields" in "\n".join(worker.lines)
            assert "reason=invalid_sample_order_or_interval" in "\n".join(worker.lines)
            with sqlite3.connect(db_path) as conn:
                rows = conn.execute("SELECT COUNT(*),SUM(hardware_validated) FROM predictions").fetchone()
                assert rows == (25, 0), rows
                assert conn.execute("SELECT COUNT(*) FROM messages WHERE device_id='live-duplicate'").fetchone()[0] == 8
            report["prediction_count"] = 25
            report["http_latest"] = http.get("/api/activities/latest").json()
            report["http_history"] = http.get("/api/activities/history?limit=100").json()
            assert len(report["http_history"]["items"]) == 25

        # Isolate the regression publisher from the already verified process-test DB.
        api.stop()
        worker.stop()
        # Run the full regression suite, including the formerly skipped real broker test.
        regression_env = dict(env, RUN_LIVE_MQTT="1")
        for name, command in (("backend", ["-m", "unittest", "discover", "-s", "backend/tests", "-v"]),
                              ("ml", ["-m", "unittest", "discover", "-s", "ml", "-p", "test_baseline.py", "-v"]),
                              ("offline", ["-m", "backend.demo"])):
            check = Process([sys.executable, "-B", *command], folder / f"tests-{name}.log", regression_env)
            try:
                check.wait(timeout=60)
                report[f"{name}_validation"] = "passed"
            finally:
                check.stop()
            print(f"PASS {name} validation", flush=True)
        report["status"] = "passed"
    except BaseException as exc:
        report["status"] = "failed"
        report["error"] = str(exc)
        raise
    finally:
        for process, signal in ((api, None), (worker, None), (broker, signal_tool)):
            if process:
                process.stop(signal)
        report["owned_services_stopped"] = all(p is None or p.process.poll() is not None for p in (api, worker, broker))
        (folder / "results.json").write_text(json.dumps(report, indent=2) + "\n")
        print(f"Evidence saved: {folder.relative_to(PROJECT)}/results.json; services stopped", flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--broker", type=Path,
        default=ROOT / "runtime" / "mosquitto-2.1.2" / "files" / "mosquitto.exe")
    run(parser.parse_args().broker)


if __name__ == "__main__":
    main()
