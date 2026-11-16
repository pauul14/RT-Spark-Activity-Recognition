"""Download only the professor's Kaggle dataset, without credentials or alternatives."""
import argparse
import hashlib
import io
import json
from pathlib import Path
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parent
EXPECTED_SHA256 = "56ab5e861da22f7b1b4eaefa2d6fdc170f6febc2cf03d5a1883a180dab083271"
API = "https://www.kaggle.com/api/v1/datasets"


def download(destination=ROOT / "data"):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    try:
        def fetch(url):
            with urlopen(Request(url, headers={"User-Agent": "BCA182-Lab3/1.0"}), timeout=90) as response:
                return response.read()

        # Pin the data version; never silently accept a different CSV.
        archive = fetch(f"{API}/download/vmalyi/run-or-walk?datasetVersionNumber=2")
        with zipfile.ZipFile(io.BytesIO(archive)) as dataset:
            raw = dataset.read("dataset.csv")
        if hashlib.sha256(raw).hexdigest() != EXPECTED_SHA256:
            raise ValueError("dataset.csv checksum differs from the reviewed version 2")
        metadata_raw = fetch(f"{API}/view/vmalyi/run-or-walk")
        metadata = json.loads(metadata_raw)
        if metadata.get("licenseName") != "CC BY-NC-SA 4.0":
            raise ValueError("Dataset license changed; review it before downloading again")
    except Exception as exc:
        raise SystemExit(
            f"Dataset access failed: {exc}\n"
            "Obtain dataset.csv from vmalyi/run-or-walk version 2 on Kaggle and place it in ml/data/. "
            "No substitute or synthetic training data will be used."
        ) from exc
    (destination / "run-or-walk.zip").write_bytes(archive)
    (destination / "dataset.csv").write_bytes(raw)
    (destination / "kaggle-metadata.json").write_bytes(metadata_raw)
    print(f"Saved verified dataset.csv: {len(raw)} bytes, SHA256={EXPECTED_SHA256}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data")
    download(parser.parse_args().output)
