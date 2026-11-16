"""CONNACK rejection reporting, bounded retry and credential hygiene. No network access."""
from dataclasses import replace
import contextlib
import io
import os
import ssl
import unittest
from unittest.mock import Mock, patch

import paho.mqtt.client as mqtt
from paho.mqtt.client import convert_connack_rc_to_reason_code
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode

from backend.codespaces import mqtt_diagnose
from backend.config import Settings
from backend.mqtt_transport import classify_connack
from backend.mqtt_worker import MQTTWorker

SECRET = "test-only-secret-value"


def secure(**changes):
    values = dict(mqtt_host="deployment.example.com", mqtt_port=8883, mqtt_tls=True, mqtt_username="test-worker",
                  mqtt_password=SECRET, mqtt_device_ids=("rtspark-test",))
    values.update(changes)
    return Settings(**values)


class ClassificationTests(unittest.TestCase):
    def test_mqtt311_return_codes(self):
        expected = {1: "protocol_mismatch", 2: "invalid_client_id", 3: "server_unavailable",
                    4: "bad_username_or_password", 5: "not_authorized"}
        for code, category in expected.items():
            with self.subTest(code=code):
                self.assertEqual(classify_connack(convert_connack_rc_to_reason_code(code))[0], category)
                self.assertEqual(classify_connack(code)[0], category)  # raw integer form

    def test_mqtt5_reason_codes(self):
        expected = {134: "bad_username_or_password", 135: "not_authorized", 138: "banned", 133: "invalid_client_id",
                    132: "protocol_mismatch", 136: "server_unavailable", 151: "quota_exceeded", 128: "unexpected"}
        for code, category in expected.items():
            with self.subTest(code=code):
                self.assertEqual(classify_connack(ReasonCode(PacketTypes.CONNACK, identifier=code))[0], category)

    def test_only_server_side_conditions_are_retried(self):
        for code in (1, 2, 4, 5):
            self.assertTrue(classify_connack(code)[1])
        self.assertFalse(classify_connack(3)[1])
        self.assertEqual(classify_connack(ReasonCode(PacketTypes.CONNACK, "Success"))[0], "accepted")


class WorkerRejectionTests(unittest.TestCase):
    def worker(self):
        service = Mock()
        client = Mock()
        return MQTTWorker(secure(), service, client=client), client

    def test_permanent_rejection_logs_reason_and_stops(self):
        worker, client = self.worker()
        with self.assertLogs("backend.mqtt_worker", "WARNING") as logs:
            worker.on_connect(client, None, None, convert_connack_rc_to_reason_code(4), None)
        text = "\n".join(logs.output)
        self.assertIn("reason='Bad user name or password' code=134 category=bad_username_or_password retry=stopped", text)
        self.assertNotIn(SECRET, text)
        client.disconnect.assert_called_once()
        client.subscribe.assert_not_called()
        self.assertEqual(worker.stopped_reason, "bad_username_or_password")

    def test_server_unavailable_keeps_backoff(self):
        worker, client = self.worker()
        with self.assertLogs("backend.mqtt_worker", "WARNING") as logs:
            worker.on_connect(client, None, None, convert_connack_rc_to_reason_code(3), None)
        self.assertIn("retry=backoff", logs.output[0])
        client.disconnect.assert_not_called()
        self.assertIsNone(worker.stopped_reason)

    def test_connect_failures_are_logged_with_bounded_frequency(self):
        worker, client = self.worker()
        with self.assertLogs("backend.mqtt_worker", "WARNING") as logs:
            for _ in range(60):
                worker.on_connect_fail(client, None)
        self.assertEqual(len(logs.output), 4)  # attempts 1, 3, 10, 50
        self.assertIn("cause=network_dns_or_tls", logs.output[0])


class CredentialHygieneTests(unittest.TestCase):
    def test_pasted_defects_rejected_without_revealing_value(self):
        for bad in (SECRET + "\n", SECRET + "\r\n", " " + SECRET, SECRET + " ", '"' + SECRET + '"',
                    "'" + SECRET + "'", "test\tsecret"):
            for field in ("mqtt_password", "mqtt_username"):
                with self.subTest(field=field, value=repr(bad)), self.assertRaises(ValueError) as caught:
                    secure(**{field: bad})
                self.assertNotIn(SECRET, str(caught.exception))
                self.assertNotIn("test\tsecret", str(caught.exception))

    def test_from_env_reports_the_variable_name(self):
        env = {"MQTT_HOST": "deployment.example.com", "MQTT_TLS": "true", "MQTT_USERNAME": "test-worker",
               "MQTT_PASSWORD": SECRET + "\n"}
        with patch.dict(os.environ, env, clear=True), self.assertRaisesRegex(ValueError, "^MQTT_PASSWORD has leading"):
            Settings.from_env()

    def test_ordinary_credentials_still_accepted(self):
        secure(mqtt_password="pa ss\"word'with-inner-specials")


class FakeClient:
    """Paho stand-in: records construction and plays one scripted broker exchange."""

    def __init__(self, script, *args, **kwargs):
        self.script, self.kwargs, self.subscribed, self.disconnected = script, kwargs, None, False
        self.on_connect = self.on_subscribe = self.on_disconnect = None
        self.connect_timeout = None

    def tls_set_context(self, context):
        self.context = context

    def username_pw_set(self, username, password):
        self.username = username

    def connect(self, host, port, keepalive):
        if "error" in self.script:
            raise self.script["error"]

    def socket(self):
        return None

    def loop(self, timeout):
        if "connack" in self.script and self.on_connect and not getattr(self, "acked", False):
            self.acked = True
            self.on_connect(self, None, None, self.script["connack"], None)
        elif self.subscribed and "suback" in self.script and not getattr(self, "subacked", False):
            self.subacked = True
            self.on_subscribe(self, None, 1, self.script["suback"], None)
        elif self.disconnected:
            self.on_disconnect(self, None, None, 0, None)
        return mqtt.MQTT_ERR_SUCCESS

    def subscribe(self, topics):
        self.subscribed = topics

    def disconnect(self):
        self.disconnected = True


class DiagnoseTests(unittest.TestCase):
    def run_case(self, script, **options):
        made = []

        def factory(*args, **kwargs):
            made.append(FakeClient(script, *args, **kwargs))
            return made[-1]

        clock = iter(range(0, 10000)).__next__
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            category = mqtt_diagnose.diagnose(secure(), client_factory=factory, clock=clock, wait=20, **options)
        self.assertNotIn(SECRET, output.getvalue())
        return category, made[0], output.getvalue()

    def test_single_attempt_without_reconnect_or_downgrade(self):
        category, client, _ = self.run_case({"connack": convert_connack_rc_to_reason_code(4)})
        self.assertEqual(category, "bad_username_or_password")
        self.assertFalse(client.kwargs["reconnect_on_failure"])
        self.assertTrue(client.disconnected)

    def test_mqtt5_uses_clean_start_default(self):
        _, client, _ = self.run_case({"connack": ReasonCode(PacketTypes.CONNACK, "Success")}, protocol=mqtt.MQTTv5)
        self.assertIsNone(client.kwargs["clean_session"])
        self.assertEqual(client.kwargs["protocol"], mqtt.MQTTv5)

    def test_subscription_failure_reported(self):
        denied = [ReasonCode(PacketTypes.SUBACK, identifier=0x80)]
        category, client, text = self.run_case({"connack": ReasonCode(PacketTypes.CONNACK, "Success"), "suback": denied},
                                               subscribe=True)
        self.assertEqual(category, "subscription_rejected")
        self.assertEqual(client.subscribed, [("bca182/v1/devices/rtspark-test/motion", 1)])

    def test_subscription_ready(self):
        granted = [ReasonCode(PacketTypes.SUBACK, identifier=1)]
        category, _, text = self.run_case({"connack": ReasonCode(PacketTypes.CONNACK, "Success"), "suback": granted},
                                          subscribe=True)
        self.assertEqual(category, "accepted")
        self.assertIn("mqtt_subscription_ready_equivalent", text)

    def test_transport_failures_classified(self):
        cases = {"tls_certificate_or_hostname": ssl.SSLCertVerificationError(1, "verify failed"),
                 "dns": __import__("socket").gaierror(-2, "Name or service not known"),
                 "tcp_refused": ConnectionRefusedError(), "tcp_timeout": TimeoutError()}
        for expected, error in cases.items():
            with self.subTest(expected):
                self.assertEqual(self.run_case({"error": error})[0], expected)

    def test_no_connack_is_bounded(self):
        category, client, _ = self.run_case({})
        self.assertEqual(category, "no_connack")
        self.assertTrue(client.disconnected)

    def test_expected_username_prints_only_boolean(self):
        _, _, text = self.run_case({"connack": ReasonCode(PacketTypes.CONNACK, "Success")}, expect_username="test-worker")
        self.assertIn("username_matches_expected=True", text)


if __name__ == "__main__":
    unittest.main()
