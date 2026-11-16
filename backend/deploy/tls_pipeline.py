"""Local TLS acceptance checks using the real application transport and saved model."""
from copy import deepcopy
from dataclasses import replace
import json
import os
import socket
import ssl
import sys
from threading import Condition, Event, Thread
import warnings
import gc
from unittest.mock import patch

import httpx
import paho.mqtt.client as mqtt

from backend.database import Database
from backend.inference import Model
from backend.live_local_check import Process, free_port
from backend.mqtt_transport import configure_client
from backend.mqtt_worker import MQTTWorker, single_worker
from backend.publisher import fixture_messages, publish
from backend.service import IngestionService


def verify_tls(settings, certificate, key):
    """Negative handshakes use loopback even when testing a mismatched DNS name."""
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always", ResourceWarning)
        for values in ({"mqtt_ca_file": None}, {"mqtt_host": "wrong.example"}):
            client = configure_client(mqtt.Client(mqtt.CallbackAPIVersion.VERSION2), replace(settings, **values))
            original_connect = socket.create_connection
            try:
                # Override only the TCP destination, not TLS/SNI/certificate verification.
                with patch("socket.create_connection", side_effect=lambda address, *args, **kwargs:
                           original_connect(("127.0.0.1", settings.mqtt_port), *args, **kwargs)):
                    try:
                        client.connect(replace(settings, **values).mqtt_host, settings.mqtt_port)
                    except ssl.SSLCertVerificationError:
                        pass
                    else:
                        raise AssertionError("Application Paho transport accepted an invalid server certificate")
            finally:
                client.disconnect()
        del client
        gc.collect()
        assert not any(issubclass(item.category, ResourceWarning) for item in captured), "TLS rejection leaked a socket"

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(certificate), str(key))
    names, failures = [], []
    context.set_servername_callback(lambda connection, name, ctx: names.append(name))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)
        def accept():
            try:
                connection, _ = listener.accept()
                with connection, context.wrap_socket(connection, server_side=True) as secured:
                    secured.settimeout(5)
                    secured.recv(4096)  # Drain the real Paho MQTT CONNECT after TLS.
            except Exception as exc:
                failures.append(type(exc).__name__)
        thread = Thread(target=accept, daemon=True)
        thread.start()
        client = configure_client(mqtt.Client(mqtt.CallbackAPIVersion.VERSION2), settings)
        try:
            client.connect("localhost", listener.getsockname()[1])
        finally:
            client.disconnect()
            thread.join(6)
        assert not thread.is_alive() and not failures and names == ["localhost"], (names, failures)
    return ["paho_untrusted_certificate_rejected", "paho_wrong_hostname_rejected", "paho_sni_verified"]


def verify_pipeline(settings, folder, demo_password, restart_broker):
    """One real worker network loop, TLS publisher, SQLite, and protected HTTP API."""
    database = Database(settings.database_path)
    model = Model(settings.model_path)
    service = IngestionService(database, model, allow_demo=True)
    worker = MQTTWorker(settings, service)
    subscribed, disconnected, changed = Event(), Event(), Condition()
    processed, failures = [], []
    original_message, original_subscribe, original_disconnect = (
        worker.on_message, worker.on_subscribe, worker.on_disconnect)
    def message(*args):
        original_message(*args)
        body = json.loads(args[-1].payload)
        with changed:
            processed.append((body["device_id"], body["boot_id"], body["seq"]))
            changed.notify_all()
    def subscribe(*args):
        original_subscribe(*args)
        if all(not getattr(code, "is_failure", False) for code in args[3]):
            subscribed.set()
    def disconnect(*args):
        original_disconnect(*args)
        subscribed.clear()
        disconnected.set()
    worker.client.on_message, worker.client.on_subscribe, worker.client.on_disconnect = message, subscribe, disconnect
    def run():
        try:
            worker.run()
        except Exception as exc:
            failures.append(type(exc).__name__)
    def received(boot, sequence):
        with changed:
            assert changed.wait_for(lambda: ("dataset-demo", boot, sequence) in processed, timeout=10), "TLS worker did not process batch"
    api, thread = None, None
    env = dict(os.environ)
    env.update(APP_ENV="production", DATABASE_PATH=str(settings.database_path), MODEL_PATH=str(settings.model_path),
        MQTT_HOST=settings.mqtt_host, MQTT_PORT=str(settings.mqtt_port), MQTT_TLS="true",
        MQTT_CA_FILE=str(settings.mqtt_ca_file), MQTT_USERNAME=settings.mqtt_username,
        MQTT_PASSWORD=settings.mqtt_password, API_TOKEN=settings.api_token, API_PORT=str(settings.api_port),
        ALLOW_DATASET_DEMO="true", PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
    publisher_settings = replace(settings, mqtt_username="bca-demo", mqtt_password=demo_password)
    try:
        with single_worker(settings.database_path.with_suffix(".worker.lock")):
            thread = Thread(target=run, daemon=True)
            thread.start()
            assert subscribed.wait(10), "Application TLS subscription timed out"
            api = Process([sys.executable, "-B", "-m", "backend.api"], folder / "api.log", env)
            api.wait_log(lambda lines: any("Uvicorn running on" in line for line in lines))
            with httpx.Client(base_url=f"http://127.0.0.1:{settings.api_port}", trust_env=False, timeout=5) as http:
                assert http.get("/health").status_code == 401
                http.headers["Authorization"] = "Bearer " + settings.api_token
                assert http.get("/health").json()["database"] == "ok"
                topic, messages = fixture_messages(boot_id="tls-valid")
                publish(publisher_settings, topic, messages, scenario="duplicate", interval=1)
                received("tls-valid", 7)
                records = http.get("/api/activities/history?device_id=dataset-demo&limit=100").json()["items"]
                assert len(records) == 5 and all(not row["hardware_validated"] for row in records)
                assert len({(row["boot_id"], row["device_timestamp_ms"], row["model_id"]) for row in records}) == 5
                assert http.get("/api/activities/latest?device_id=dataset-demo").json() == records[0]
                expected = model.predict([sample for body in messages[4:8] for sample in body["samples"]])
                assert records[0]["activity"] == expected["activity"]
                assert abs(records[0]["running_probability"] - expected["probabilities"]["running"]) < 1e-12

                recovered = deepcopy(messages)
                for body in recovered:
                    body["boot_id"] = "tls-broker-recovery"
                publish(publisher_settings, topic, recovered[:3], interval=1)
                received("tls-broker-recovery", 2)
                assert len(database.history(limit=100)["items"]) == 5
                disconnected.clear()
                restart_broker(disconnected)  # Stop, observe disconnect, start the owned broker.
                assert subscribed.wait(10), "TLS worker did not reconnect and re-subscribe"
                publish(publisher_settings, topic, recovered[2:7], interval=1)
                received("tls-broker-recovery", 6)
                result = http.get("/api/activities/history?device_id=dataset-demo&limit=100").json()["items"]
                assert len(result) == 6 and result[0]["sequence_end"] == 6
                assert sum(row["boot_id"] == "tls-broker-recovery" for row in result) == 1
                assert all(row["hardware_validated"] is False and row["inference_mode"] == "dataset_demo" for row in result)
    finally:
        worker.stop()
        if thread:
            thread.join(10)
        if api:
            api.stop()
    assert thread is not None and not thread.is_alive() and not failures, failures
    # The actual network loop stopped, disconnected and cleared its buffers.
    assert service.buffers == {}
    with single_worker(settings.database_path.with_suffix(".worker.lock")):
        pass
    return {"predictions": 6, "duplicate_predictions": 5, "recovery_predictions": 1,
            "authenticated_api": "passed", "worker_stopped_and_lock_released": True,
            "api_stopped": api.process.poll() is not None, "hardware_validated": False}
