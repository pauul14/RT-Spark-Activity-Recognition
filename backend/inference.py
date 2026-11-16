"""Read-only adapter to the unchanged ML Predictor."""
import hashlib
import importlib
from pathlib import Path
import sys

from .config import PROJECT
from .validation import AXES


class Model:
    def __init__(self, model_path):
        ml_path = str(PROJECT / "ml")
        if ml_path not in sys.path:
            sys.path.insert(0, ml_path)
        self.predictor = importlib.import_module("baseline").Predictor(model_path)
        digest = hashlib.sha256(Path(model_path).read_bytes()).hexdigest()
        self.model_id = f"sha256:{digest}:approximately_4s_v1"

    def predict(self, samples):
        origin = samples[0]["t_ms"]
        return self.predictor.predict_window({"input_contract": "kaggle_vmalyi_v2_native", "samples": [
            {"t_seconds": (sample["t_ms"] - origin) / 1000,
             **{axis: sample[axis] for axis in AXES}} for sample in samples]})
