"""Windows-only localhost validation of the deployment broker's TLS/auth/ACL rules.

Supply a disposable self-signed certificate/key created outside this script.
All listeners are rebound to loopback; no public service or persistent credential is created.
"""
import argparse
import gc
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
import uuid
from threading import Event

import paho.mqtt.client as mqtt

from backend.config import ROOT, Settings
from backend.deploy.tls_pipeline import verify_pipeline, verify_tls
from backend.live_local_check import Process, free_port


def run(certificate, key):
    folder = certificate.resolve().parent / ("run-" + uuid.uuid4().hex[:8])
    folder.mkdir()
    print("Isolated local TLS evidence:", folder, flush=True)
    broker = ROOT / "runtime/mosquitto-2.1.2/files/mosquitto.exe"
    passwords = folder / "passwords"
    worker_password, demo_password = secrets.token_hex(32), secrets.token_hex(32)
    for index, (user, password) in enumerate((("bca-worker", worker_password), ("bca-demo", demo_password))):
        command = [str(broker.with_name("mosquitto_passwd.exe")), "-H", "sha512-pbkdf2", "-b"]
        if index == 0:
            command.append("-c")
        result = subprocess.run([*command, str(passwords), user, password], capture_output=True,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if result.returncode:
            raise RuntimeError("Disposable password-file creation failed: " +
                               result.stderr.decode(errors="replace").replace(password, "[withheld]"))
    plain_port, tls_port = free_port(18884), free_port(18885)
    template = (ROOT / "deploy/mosquitto.conf").read_text()
    replacements = {"user mosquitto\n": "", "listener 1883 127.0.0.1": f"listener {plain_port} 127.0.0.1",
        "listener 8883 0.0.0.0": f"listener {tls_port} 127.0.0.1",
        "/etc/mosquitto/bca182/passwords": passwords.as_posix(),
        "/etc/mosquitto/bca182/acl": (ROOT / "deploy/mqtt.acl").as_posix(),
        "/etc/mosquitto/bca182/tls/fullchain.pem": certificate.resolve().as_posix(),
        "/etc/mosquitto/bca182/tls/privkey.pem": key.resolve().as_posix(),
        "/var/lib/mosquitto/": folder.as_posix() + "/"}
    for before, after in replacements.items():
        template = template.replace(before, after)
    conf = folder / "mosquitto.conf"
    conf.write_text(template)
    process = Process([str(broker), "-c", str(conf), "-v"], folder / "broker.log", dict(os.environ))
    clients, checks, pipeline = [], [], None
    status = "failed"

    def connect(user=None, password=None, expect_success=True):
        ready, result = Event(), []
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv5)
        client.tls_set(ca_certs=str(certificate), cert_reqs=ssl.CERT_REQUIRED)
        if user:
            client.username_pw_set(user, password)
        def connected(c, userdata, flags, reason_code, properties):
            result.append(not reason_code.is_failure)
            ready.set()
        client.on_connect = connected
        clients.append(client)
        client.connect("127.0.0.1", tls_port)
        client.loop_start()
        assert ready.wait(5) and result == [expect_success], "Unexpected MQTT authentication outcome"
        return client

    def subscribe(client, topic, accepted):
        ready, result = Event(), []
        def subscribed(c, userdata, mid, reason_codes, properties):
            result.append(all(not code.is_failure for code in reason_codes))
            ready.set()
        client.on_subscribe = subscribed
        client.subscribe(topic, qos=1)
        assert ready.wait(5) and result == [accepted], "Unexpected SUBACK authorization"

    def publish(client, topic, accepted):
        ready, result = Event(), []
        def published(c, userdata, mid, reason_code, properties):
            result.append(not reason_code.is_failure)
            ready.set()
        client.on_publish = published
        client.publish(topic, b"ACL transport probe only", qos=1)
        assert ready.wait(5) and result == [accepted], "Unexpected PUBACK authorization"

    try:
        process.wait_log(lambda lines: any("running" in line for line in lines))
        assert not any("default listener" in line for line in process.lines), "Unexpected implicit listener"
        settings = Settings(database_path=folder / "activities.sqlite3", mqtt_host="localhost",
            mqtt_port=tls_port, mqtt_tls=True, mqtt_ca_file=certificate.resolve(),
            mqtt_username="bca-worker", mqtt_password=worker_password, environment="production",
            api_token=secrets.token_hex(32), allow_dataset_demo=True, api_port=free_port(8001))
        checks.extend(verify_tls(settings, certificate.resolve(), key.resolve()))
        for context, hostname in ((ssl.create_default_context(), "localhost"),
                                  (ssl.create_default_context(cafile=str(certificate)), "wrong.invalid")):
            with socket.create_connection(("127.0.0.1", tls_port), timeout=5) as connection:
                try:
                    with context.wrap_socket(connection, server_hostname=hostname):
                        raise AssertionError("Untrusted certificate or wrong hostname was accepted")
                except ssl.SSLCertVerificationError:
                    pass
        checks.extend(["untrusted_certificate_rejected", "wrong_hostname_rejected"])
        connect(expect_success=False)
        connect("bca-demo", "incorrect-password", expect_success=False)
        checks.extend(["anonymous_rejected", "wrong_password_rejected"])
        worker = connect("bca-worker", worker_password)
        demo = connect("bca-demo", demo_password)
        topic = "bca182/demo/v1/devices/dataset-demo/motion"
        received = Event()
        worker.on_message = lambda *args: received.set()
        subscribe(worker, "bca182/demo/v1/devices/+/motion", True)
        publish(demo, topic, True)
        assert received.wait(5), "Authorized TLS payload was not delivered"
        checks.append("authenticated_tls_delivery")
        publish(demo, "bca182/demo/v1/devices/another-device/motion", False)
        publish(demo, "bca182/v1/devices/spark-001/motion", False)
        publish(worker, topic, False)
        # Mosquitto can grant a wildcard subscription yet suppress unauthorized
        # deliveries; publish denial is asserted using explicit MQTT v5 PUBACKs.
        checks.extend(["demo_other_device_denied", "demo_hardware_topic_denied", "worker_publish_denied"])
        for client in clients:
            client.disconnect()
            client.loop_stop()
        clients.clear()
        def restart_broker(disconnected):
            nonlocal process
            process.stop(broker.with_name("mosquitto_signal.exe"))
            assert disconnected.wait(10), "Worker did not detect controlled broker shutdown"
            process = Process([str(broker), "-c", str(conf), "-v"], folder / "broker-restarted.log", dict(os.environ))
            process.wait_log(lambda lines: any("running" in line for line in lines))
        pipeline = verify_pipeline(settings, folder, demo_password, restart_broker)
        status = "passed"
    finally:
        for client in clients:
            client.disconnect()
            client.loop_stop()
        clients.clear()
        gc.collect()
        process.stop(broker.with_name("mosquitto_signal.exe"))
        result = {"status": status, "checks": checks, "pipeline": pipeline,
                  "owned_broker_stopped": process.process.poll() is not None,
                  "scope": "Windows loopback TLS/auth/ACL and application pipeline; no hardware or cloud"}
        (folder / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    print("Evidence saved:", folder / "results.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("certificate", type=Path)
    parser.add_argument("key", type=Path)
    args = parser.parse_args()
    run(args.certificate, args.key)
