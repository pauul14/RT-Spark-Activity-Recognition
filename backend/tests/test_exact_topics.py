"""Ensure subscription filters actually match the documented per-device ACL."""
from dataclasses import replace
import os,unittest
from unittest.mock import Mock,patch
from backend.config import Settings
from backend.mqtt_worker import MQTTWorker

class ExactTopicTests(unittest.TestCase):
    def test_exact_subscription_and_demo_namespace(self):
        for demo in (False,True):
            settings=Settings(mqtt_device_ids=('board-a','board-b'),allow_dataset_demo=demo)
            client,service=Mock(),Mock()
            worker=MQTTWorker(settings,service,client)
            worker.on_connect(client,None,None,0,None)
            expected=[('bca182/v1/devices/board-a/motion',1),('bca182/v1/devices/board-b/motion',1)]
            if demo:expected.append(('bca182/demo/v1/devices/dataset-demo/motion',1))
            client.subscribe.assert_called_once_with(expected)
    def test_local_legacy_wildcard_remains_compatible(self):
        client=Mock();worker=MQTTWorker(Settings(),Mock(),client)
        worker.on_connect(client,None,None,0,None)
        client.subscribe.assert_called_once_with([('bca182/v1/devices/+/motion',1)])
    def test_invalid_duplicate_or_wildcard_ids_rejected(self):
        for devices in (('board','board'),('+',),('#',),('a/b',),('',),tuple(str(i) for i in range(17))):
            with self.subTest(devices=devices),self.assertRaises(ValueError):Settings(mqtt_device_ids=devices)
    def test_environment_is_exact_and_fail_closed(self):
        with patch.dict(os.environ,{'MQTT_DEVICE_IDS':' board-a,board-b '},clear=True):
            self.assertEqual(Settings.from_env().mqtt_device_ids,('board-a','board-b'))
        for value in ('','board-a,','board-a,,board-b','board-a,board-a','+'):
            with patch.dict(os.environ,{'MQTT_DEVICE_IDS':value},clear=True),self.assertRaises(ValueError):Settings.from_env()
