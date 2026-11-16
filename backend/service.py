"""Atomic message deduplication, rolling windows, inference, and persistence."""
import time
from datetime import datetime, timezone
from dataclasses import replace

from .buffering import WindowBuffer
from .validation import InvalidPayload, TOPIC, validate

KEEP = object()


class IngestionService:
    def __init__(self, database, model, *, allow_demo=False, clock=time.monotonic, board_model=None, archive_telemetry=False):
        self.database = database
        self.model = model
        self.allow_demo = allow_demo
        self.clock = clock
        self.buffers = {}
        self.board_model=board_model
        self.archive_telemetry=archive_telemetry

    def connection_reset(self):
        """Drop ambiguous partial windows; retain SQLite sequence/digest history."""
        self.buffers.clear()

    def expire_idle(self):
        now = self.clock()
        for device in list(self.buffers):
            if now - self.buffers[device].last_seen > 2:
                del self.buffers[device]

    def ingest(self, topic, payload, *, retained=False):
        try:
            message = validate(topic, payload, allow_demo=self.allow_demo, retained=retained)
        except InvalidPayload as exc:
            match = TOPIC.fullmatch(topic)
            if match:
                self.buffers.pop(match["device"], None)
            return {"status": "rejected", "reason": str(exc)}
        now = self.clock()
        # The ledger, sequence state and prediction commit together. A failed transaction
        # leaves the in-memory buffer untouched, so a retry cannot skip a failed insert.
        with self.database.transaction() as conn:
            outcome, buffer = self._process(conn, message, now)
            if self.archive_telemetry and outcome['status'] not in ('rejected','duplicate'):
                # Untouched admitted bytes, committed with dedup/inference state.
                # This is transport evidence, never a substitute for labelled UART proof.
                conn.execute('INSERT INTO telemetry_archive VALUES (?,?,?,?,?,?)',
                    (message.device_id,message.boot_id,message.seq,topic,payload,
                     datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')))
        if buffer is None:
            self.buffers.pop(message.device_id, None)
        elif buffer is not KEEP:
            self.buffers[message.device_id] = buffer
        self.expire_idle()
        if len(self.buffers)>128:
            oldest=min(self.buffers,key=lambda device:self.buffers[device].last_seen)
            del self.buffers[oldest] # explicit capacity bound; ledger remains persistent
        return {"device_id": message.device_id, "seq": message.seq, "mode": outcome.get('inference_mode',message.mode), **outcome}

    def _process(self, conn, message, now):
        device, boot, seq = message.device_id, message.boot_id, message.seq
        ledger = conn.execute("SELECT digest FROM messages WHERE device_id=? AND boot_id=? AND seq=?",
                              (device, boot, seq)).fetchone()
        if ledger:
            if ledger["digest"] == message.digest:
                return {"status": "duplicate"}, KEEP
            return {"status": "rejected", "reason": "conflicting_redelivery"}, None
        state = conn.execute("SELECT * FROM devices WHERE device_id=?", (device,)).fetchone()
        known_boot = conn.execute("SELECT 1 FROM boots WHERE device_id=? AND boot_id=?", (device, boot)).fetchone()
        if state and state["boot_id"] != boot and known_boot:
            return {"status": "rejected", "reason": "retired_boot"}, KEEP
        resets = []
        buffer = self.buffers.get(device)
        if state is None or state["boot_id"] != boot:
            resets.append("new_boot")
            buffer = None
        else:
            if seq <= state["last_seq"]:
                return {"status": "rejected", "reason": "out_of_order_sequence"}, KEEP
            if message.samples[0]["t_ms"] <= state["last_t_ms"]:
                return {"status": "rejected", "reason": "duplicate_or_decreasing_timestamp"}, None
            if seq != state["last_seq"] + 1:
                resets.append("missing_sequence")
                buffer = None
            if not 100 <= message.samples[0]["t_ms"] - state["last_t_ms"] <= 300:
                resets.append("sample_gap")
                buffer = None
            if message.fingerprint != state["fingerprint"]:
                resets.append("metadata_changed")
                buffer = None
        if buffer and now - buffer.last_seen > 2:
            resets.append("idle_timeout")
            buffer = None
        if buffer is None:
            buffer = WindowBuffer(boot, message.fingerprint, now)
        buffer = buffer.append(message.samples, now)
        outcome = {"status": "buffering", "samples_buffered": len(buffer.samples), "resets": resets}
        if len(buffer.samples) == 20:
            if not buffer.valid_window():
                outcome.update(status="rejected_window", reason="window_timing_invalid")
                buffer = None
            elif message.mode == "hardware_unvalidated" and (self.board_model is None or not self.board_model.accepts(message)):
                outcome.update(status="hardware_unvalidated", reason="sensor_model_adapter_unverified")
            else:
                # Only validated demo metadata on the opt-in demo namespace reaches the model.
                try:
                    selected=self.model if message.mode=='dataset_demo' else self.board_model
                    prediction = selected.predict(buffer.samples)
                except ValueError:
                    outcome.update(status="rejected_window", reason="model_input_rejected")
                    buffer = None
                else:
                    if message.mode=='hardware_unvalidated': message=replace(message,mode='board_validated')
                    record_id = self.database.insert_prediction(conn, message, buffer.samples, prediction, selected.model_id)
                    outcome.update(status="prediction_stored" if record_id else "prediction_already_stored",
                                   record_id=record_id, inference_mode=message.mode,hardware_validated=message.mode=='board_validated')
        elif message.mode == "hardware_unvalidated":
            outcome.update(status="hardware_unvalidated", reason="sensor_model_adapter_unverified")
        conn.execute("INSERT OR IGNORE INTO boots(device_id,boot_id) VALUES (?,?)", (device, boot))
        conn.execute("INSERT INTO messages(device_id,boot_id,seq,digest) VALUES (?,?,?,?)",
                     (device, boot, seq, message.digest))
        conn.execute("""INSERT INTO devices(device_id,boot_id,last_seq,last_t_ms,fingerprint) VALUES (?,?,?,?,?)
            ON CONFLICT(device_id) DO UPDATE SET boot_id=excluded.boot_id,last_seq=excluded.last_seq,
            last_t_ms=excluded.last_t_ms,fingerprint=excluded.fingerprint""",
            (device, boot, seq, message.samples[-1]["t_ms"], message.fingerprint))
        self.database.update_status(conn,message)
        return outcome, buffer
