"""One dedicated Paho subscriber. The API never starts a subscriber."""
from contextlib import contextmanager
import logging
import os
import signal

import paho.mqtt.client as mqtt

from .config import Settings
from .database import Database
from .inference import Model
from .mqtt_transport import classify_connack, configure_client
from .service import IngestionService

LOG = logging.getLogger(__name__)

def load_board_model(settings):
    if settings.board_model_dir is None:
        return None
    if settings.board_model_version == "three-class-v2":
        from .three_class_model import ThreeClassBoardModel, require_activation
        require_activation(settings.board_activation_file)
        return ThreeClassBoardModel(settings.board_model_dir, settings.board_placement_id)
    from .board_model import ValidatedBoardModel
    return ValidatedBoardModel(settings.board_model_dir,settings.board_placement_id)


class MQTTWorker:
    def __init__(self, settings, service, client=None):
        self.settings, self.service = settings, service
        self.client = client or mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
            client_id=settings.mqtt_client_id, clean_session=True, protocol=mqtt.MQTTv311)
        configure_client(self.client, settings)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_connect_fail = self.on_connect_fail
        self.client.on_message = self.on_message
        self.client.on_subscribe = self.on_subscribe
        self.client.on_publish = self.on_publish
        self.client.max_queued_messages_set(32)
        self.client.max_inflight_messages_set(8)
        self.pending_mids={}
        self.subscribed=False
        self.stopped_reason=None
        self.connect_failures=0

    def on_connect(self, client, userdata, flags, reason_code, properties):
        self.subscribed=False
        self.service.connection_reset()
        if reason_code != 0:
            category, permanent, name, value = classify_connack(reason_code)
            LOG.warning("mqtt_connection_rejected reason=%r code=%s category=%s retry=%s",
                        name, value, category, "stopped" if permanent else "backoff")
            if permanent:
                # Credentials/authorization/client ID/protocol will not fix themselves:
                # stop after one CONNACK instead of reconnecting against the broker forever.
                self.stopped_reason = category
                client.disconnect()
            return
        topics = [("bca182/v1/devices/"+device+"/motion",1) for device in self.settings.mqtt_device_ids]
        if not topics: topics=[("bca182/v1/devices/+/motion",1)] # existing local multi-device template
        if self.settings.allow_dataset_demo:
            topics.append(("bca182/demo/v1/devices/dataset-demo/motion", 1))
        client.subscribe(topics)
        LOG.info("mqtt_connected subscriptions=%d", len(topics))

    def on_connect_fail(self, client, userdata):
        # TCP, DNS or TLS failure before any CONNACK. Paho 2.1.0 gives no exception here.
        self.connect_failures += 1
        if self.connect_failures in (1, 3, 10) or self.connect_failures % 50 == 0:
            LOG.warning("mqtt_connect_failed before_connack count=%d cause=network_dns_or_tls "
                        "diagnose=backend/codespaces/mqtt_diagnose.py", self.connect_failures)

    def on_subscribe(self, client, userdata, mid, reason_codes, properties):
        if any(getattr(code, "is_failure", False) for code in reason_codes):
            LOG.error("mqtt_subscription_rejected")
        else:
            self.subscribed=True
            LOG.info("mqtt_subscription_ready")
            self.flush_downlinks()

    def on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.subscribed=False
        self.pending_mids.clear()
        self.service.connection_reset()
        LOG.info("mqtt_disconnected partial_buffers_cleared")

    def on_message(self, client, userdata, message):
        try:
            outcome = self.service.ingest(message.topic, message.payload, retained=message.retain)
            LOG.info("ingest status=%s reason=%s device=%s seq=%s", outcome["status"],
                     outcome.get("reason", ""), outcome.get("device_id", ""), outcome.get("seq", ""))
            self.flush_downlinks()
        except Exception:
            # Fail the worker rather than acknowledge and silently continue after failed persistence.
            # Paho's clean session is not a durable input queue: see README delivery limitations.
            self.service.connection_reset()
            LOG.error("ingestion_failed worker_stopping")
            raise

    def flush_downlinks(self):
        if not self.subscribed:
            return
        inflight=set(self.pending_mids.values())
        for row in self.service.database.pending_downlinks():
            if len(self.pending_mids)>=16:
                break
            if row['prediction_id'] in inflight:
                continue
            info=self.client.publish(row['topic'],row['payload'],qos=1,retain=False)
            if info.rc!=mqtt.MQTT_ERR_SUCCESS:
                LOG.warning('prediction_queue_full_or_disconnected')
                break
            self.pending_mids[info.mid]=row['prediction_id']

    def on_publish(self,client,userdata,mid,reason_code=None,properties=None):
        record=self.pending_mids.pop(mid,None)
        if record is not None and not getattr(reason_code,'is_failure',False):
            self.service.database.acknowledge_downlink(record)
        self.flush_downlinks()

    def run(self):
        self.client.connect_async(self.settings.mqtt_host, self.settings.mqtt_port, keepalive=30)
        try:
            self.client.loop_forever(timeout=1, retry_first_connection=True)
        finally:
            self.stop()
        return self.stopped_reason

    def stop(self):
        self.client.disconnect()
        self.service.connection_reset()


@contextmanager
def single_worker(path):
    """Fail fast on a second worker for the same database, including on Windows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings.from_env()
    with single_worker(settings.database_path.with_suffix(".worker.lock")):
        service = IngestionService(Database(settings.database_path), Model(settings.model_path),
                                   allow_demo=settings.allow_dataset_demo,
                                   board_model=load_board_model(settings),archive_telemetry=settings.archive_telemetry)
        worker = MQTTWorker(settings, service)
        previous = signal.signal(signal.SIGTERM, lambda *_: worker.stop())
        try:
            stopped = worker.run()
        except KeyboardInterrupt:
            worker.stop()
            stopped = None
        finally:
            signal.signal(signal.SIGTERM, previous)
    if stopped:
        LOG.error("worker_stopped reason=%s; correct the configuration, then start the worker again", stopped)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
