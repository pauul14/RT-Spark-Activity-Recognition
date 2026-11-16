"""Offline callback-to-SQLite-to-API demonstration; does not use a network broker."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

from fastapi.testclient import TestClient

from .api import create_app
from .config import ROOT, Settings
from .database import Database
from .inference import Model
from .mqtt_worker import MQTTWorker
from .publisher import fixture_messages
from .service import IngestionService


def main():
    runtime = ROOT / "runtime"
    runtime.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=runtime, prefix="offline-demo-") as folder:
        settings = Settings(database_path=Path(folder) / "demo.sqlite3", allow_dataset_demo=True)
        database = Database(settings.database_path)
        service = IngestionService(database, Model(settings.model_path), allow_demo=True)
        worker = MQTTWorker(settings, service)
        topic, messages = fixture_messages()
        for body in messages:
            worker.on_message(worker.client, None, SimpleNamespace(
                topic=topic, payload=json.dumps(body).encode(), retain=False))
        with TestClient(create_app(database)) as client:
            latest = client.get("/api/activities/latest").json()
            history = client.get("/api/activities/history").json()
        print(json.dumps({"transport": "offline MQTT callback injection; no broker connection",
                          "predictions": len(history["items"]), "latest": latest}, indent=2))


if __name__ == "__main__":
    main()
