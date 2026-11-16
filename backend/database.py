"""SQLite storage shared by one MQTT worker and read-only API requests."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import math
import time
from .downlink import encode_prediction, prediction_schema

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
 device_id TEXT PRIMARY KEY, boot_id TEXT NOT NULL, last_seq INTEGER NOT NULL,
 last_t_ms INTEGER NOT NULL, fingerprint TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS boots (
 device_id TEXT NOT NULL, boot_id TEXT NOT NULL, PRIMARY KEY(device_id,boot_id)
);
CREATE TABLE IF NOT EXISTS messages (
 device_id TEXT NOT NULL, boot_id TEXT NOT NULL, seq INTEGER NOT NULL, digest TEXT NOT NULL,
 PRIMARY KEY(device_id,boot_id,seq)
);
CREATE TABLE IF NOT EXISTS telemetry_archive (
 device_id TEXT NOT NULL, boot_id TEXT NOT NULL, seq INTEGER NOT NULL,
 topic TEXT NOT NULL, payload BLOB NOT NULL, received_at TEXT NOT NULL,
 PRIMARY KEY(device_id,boot_id,seq)
);
CREATE TABLE IF NOT EXISTS predictions (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 device_id TEXT NOT NULL, boot_id TEXT NOT NULL, sequence_end INTEGER NOT NULL,
 activity TEXT NOT NULL CHECK(activity IN ('walking','running','stationary')),
 walking_probability REAL NOT NULL CHECK(walking_probability BETWEEN 0 AND 1),
 running_probability REAL NOT NULL CHECK(running_probability BETWEEN 0 AND 1),
 stationary_probability REAL NOT NULL DEFAULT 0 CHECK(stationary_probability BETWEEN 0 AND 1),
 window_start_ms INTEGER NOT NULL, device_timestamp_ms INTEGER NOT NULL,
 server_timestamp TEXT NOT NULL, model_id TEXT NOT NULL,
 inference_mode TEXT NOT NULL CHECK(inference_mode IN ('dataset_demo','board_validated')),
 hardware_validated INTEGER NOT NULL DEFAULT 0 CHECK(
   (inference_mode='dataset_demo' AND hardware_validated=0) OR
   (inference_mode='board_validated' AND hardware_validated=1)),
 UNIQUE(device_id,boot_id,device_timestamp_ms,model_id,inference_mode)
);
CREATE INDEX IF NOT EXISTS predictions_device_id ON predictions(device_id,id DESC);
CREATE TABLE IF NOT EXISTS device_status (
 device_id TEXT PRIMARY KEY, boot_id TEXT NOT NULL, sequence_end INTEGER NOT NULL,
 device_timestamp_ms INTEGER NOT NULL, mode TEXT NOT NULL, server_timestamp TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prediction_outbox (
 prediction_id INTEGER PRIMARY KEY REFERENCES predictions(id), topic TEXT NOT NULL,
 payload BLOB NOT NULL, expires_at REAL NOT NULL, delivered_at REAL
);
"""


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            conn.execute('BEGIN IMMEDIATE')
            try:
                existing=conn.execute("SELECT sql FROM sqlite_master WHERE name='predictions'").fetchone()[0]
                if 'stationary_probability' not in existing:
                    start=SCHEMA.index('CREATE TABLE IF NOT EXISTS predictions (')
                    end=SCHEMA.index(';',start)
                    create=SCHEMA[start:end].replace('IF NOT EXISTS predictions (','predictions_v2 (',1)
                    conn.execute(create)
                    columns=[row[1] for row in conn.execute('PRAGMA table_info(predictions)')]
                    names=','.join(columns)
                    conn.execute('INSERT INTO predictions_v2 ('+names+') SELECT '+names+' FROM predictions')
                    conn.execute('DROP TABLE predictions')
                    conn.execute('ALTER TABLE predictions_v2 RENAME TO predictions')
                    conn.execute('CREATE INDEX predictions_device_id ON predictions(device_id,id DESC)')
                conn.execute('PRAGMA user_version=3')
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def insert_prediction(self, conn, message, window, prediction, model_id):
        p=prediction["probabilities"]
        labels={"walking","running","stationary"} if prediction_schema(model_id)==2 else {"walking","running"}
        if (set(p)!=labels or prediction["activity"] not in labels or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1 for v in p.values()) or not math.isclose(sum(p.values()),1,abs_tol=1e-8)):
            raise ValueError("Prediction probabilities/model labels mismatch")
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        cursor = conn.execute("""INSERT INTO predictions
          (device_id,boot_id,sequence_end,activity,walking_probability,running_probability,stationary_probability,
           window_start_ms,device_timestamp_ms,server_timestamp,model_id,inference_mode,hardware_validated)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(device_id,boot_id,device_timestamp_ms,model_id,inference_mode) DO NOTHING""",
          (message.device_id, message.boot_id, message.seq, prediction["activity"],
           prediction["probabilities"]["walking"], prediction["probabilities"]["running"], prediction["probabilities"].get("stationary",0.0),
           window[0]["t_ms"], window[-1]["t_ms"], timestamp, model_id, message.mode,int(message.mode=='board_validated')))
        if not cursor.rowcount:
            return None
        row=self.record(conn.execute('SELECT * FROM predictions WHERE id=?',(cursor.lastrowid,)).fetchone())
        topic,payload=encode_prediction(row)
        conn.execute('INSERT INTO prediction_outbox(prediction_id,topic,payload,expires_at) VALUES (?,?,?,?)',
                     (row['id'],topic,payload,time.time()+10))
        # Keep classification history; bound the temporary delivery queue separately.
        conn.execute('DELETE FROM prediction_outbox WHERE prediction_id NOT IN '
                     '(SELECT prediction_id FROM prediction_outbox ORDER BY prediction_id DESC LIMIT 256)')
        return cursor.lastrowid

    def update_status(self,conn,message):
        timestamp=datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
        conn.execute('INSERT INTO device_status VALUES (?,?,?,?,?,?) ON CONFLICT(device_id) DO UPDATE SET '
                     'boot_id=excluded.boot_id,sequence_end=excluded.sequence_end,'
                     'device_timestamp_ms=excluded.device_timestamp_ms,mode=excluded.mode,server_timestamp=excluded.server_timestamp',
                     (message.device_id,message.boot_id,message.seq,message.samples[-1]['t_ms'],message.mode,timestamp))

    def current(self,device_id):
        with self.connection() as conn:
            row=conn.execute('SELECT * FROM device_status WHERE device_id=?',(device_id,)).fetchone()
        if row is None:
            return {'device_id':device_id,'state':'WAITING','activity':None,'hardware_validated':False,
                    'server_timestamp':None,'boot_id':None,'reason':'no_telemetry'}
        value=dict(row)
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(value['server_timestamp'].replace('Z','+00:00'))).total_seconds()
        value.update(state='UNVALIDATED' if -5<=age<=20 else 'OFFLINE',activity=None,hardware_validated=False,
                     reason='sensor_model_adapter_unverified' if value['mode']=='hardware_unvalidated' else
                         'dataset_demo_only' if value['mode']=='dataset_demo' else 'awaiting_fresh_reviewed_prediction')
        if value['state']!='OFFLINE' and value['mode']=='board_validated':
            prediction=self.latest(device_id)
            if prediction and prediction['boot_id']==value['boot_id'] and prediction['hardware_validated']:
                prediction_age=(datetime.now(timezone.utc)-datetime.fromisoformat(prediction['server_timestamp'].replace('Z','+00:00'))).total_seconds()
                if -5<=prediction_age<=20:
                    value.update(state=prediction['activity'].upper(),activity=prediction['activity'],hardware_validated=True,
                                 reason='explicitly_reviewed_board_model',model_id=prediction['model_id'],
                                 prediction_schema_version=prediction_schema(prediction['model_id']))
        return value

    def pending_downlinks(self,limit=16):
        with self.transaction() as conn:
            conn.execute('DELETE FROM prediction_outbox WHERE expires_at<=? OR delivered_at IS NOT NULL',(time.time(),))
            return [dict(row) for row in conn.execute('SELECT * FROM prediction_outbox ORDER BY prediction_id LIMIT ?', (limit,))]

    def acknowledge_downlink(self,prediction_id):
        with self.transaction() as conn:
            conn.execute('UPDATE prediction_outbox SET delivered_at=? WHERE prediction_id=?',(time.time(),prediction_id))

    @staticmethod
    def record(row):
        if row is None:
            return None
        value = dict(row)
        value["hardware_validated"] = bool(value["hardware_validated"])
        value["prediction_schema_version"] = prediction_schema(value["model_id"])
        return value

    def latest(self, device_id=None):
        where, params = ("WHERE device_id=?", (device_id,)) if device_id is not None else ("", ())
        with self.connection() as conn:
            return self.record(conn.execute(f"SELECT * FROM predictions {where} ORDER BY id DESC LIMIT 1", params).fetchone())

    def history(self, device_id=None, limit=50, before_id=None):
        clauses, params = [], []
        if device_id is not None:
            clauses.append("device_id=?")
            params.append(device_id)
        if before_id is not None:
            clauses.append("id<?")
            params.append(before_id)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connection() as conn:
            rows = conn.execute(f"SELECT * FROM predictions {where} ORDER BY id DESC LIMIT ?", (*params, limit + 1)).fetchall()
        more = len(rows) > limit
        items = [self.record(row) for row in rows[:limit]]
        return {"items": items, "limit": limit, "next_before_id": items[-1]["id"] if more else None}

    def healthy(self):
        with self.connection() as conn:
            conn.execute("SELECT id FROM predictions LIMIT 1").fetchall()
        return True
