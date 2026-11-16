"""Offline source-only ZIP for a future approved private Codespace transfer.
No Git command, account access, model, data, key, runtime or publication operation.
"""
import argparse,hashlib,json,zipfile
import subprocess
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def sources():
    files=[ROOT/'SOFTWARE_READY.md',ROOT/'.devcontainer/devcontainer.json',ROOT/'ml/requirements.txt',ROOT/'ml/baseline.py',
           ROOT/'ml/board_pipeline.py',ROOT/'ml/sensor_evidence.py',ROOT/'ml/release_bundle.py',ROOT/'ml/download_dataset.py',ROOT/'ml/README.md',ROOT/'ml/DATASET.md',
           ROOT/'ml/docs/MODEL_LIMITATIONS.md',ROOT/'ml/docs/SENSOR_CONTRACT.md',ROOT/'backend/deploy/backup.py']
    files+=list((ROOT/'backend').glob('*.py'))
    files+=[ROOT/'backend/requirements.txt',ROOT/'backend/deploy/model.sha256']
    files+=sorted(p for p in (ROOT/'backend/codespaces').iterdir() if p.suffix in ('.py','.md') or p.name=='app.env.example')
    return sorted(set(files))
def verify_runtime(archive):
    """Import an extracted source-only runtime; no model/data/network operation."""
    archive=Path(archive)
    with tempfile.TemporaryDirectory(prefix='source-runtime-check-',dir=archive.resolve().parent) as folder:
        root=Path(folder).resolve()
        with zipfile.ZipFile(archive) as bundle:
            for name in bundle.namelist():
                if not (root/name).resolve().is_relative_to(root): raise ValueError('Unsafe archive path')
            bundle.extractall(root)
        code=("import sys; sys.path.insert(0,'ml'); import board_pipeline,sensor_evidence; "
              "import backend.board_model,backend.mqtt_worker,backend.export_telemetry; import backend.codespaces.launch")
        result=subprocess.run([sys.executable,'-B','-c',code],cwd=root,capture_output=True,timeout=30)
        if result.returncode:
            # Show only a local failure category, never inherited environment/secrets.
            raise RuntimeError('Source runtime import failed; check transitive ML/backend dependencies')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    inventory={str(f.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(f.read_bytes()).hexdigest() for f in sources()}
    if a.output.exists() or a.output.with_suffix('.inventory.json').exists():p.error('Use a new archive filename')
    with zipfile.ZipFile(a.output,'x',compression=zipfile.ZIP_DEFLATED) as archive:
        for name in inventory:archive.write(ROOT/name,name)
        archive.writestr('.gitignore','.venv/\n__pycache__/\nbackend/runtime/\nml/data/\nml/artifacts/\nml/board-models/\n.env\n*.key\n*.pem\n*.sqlite3*\n')
    verify_runtime(a.output)
    a.output.with_suffix('.inventory.json').write_text(json.dumps({'files':inventory,'archive_sha256':hashlib.sha256(a.output.read_bytes()).hexdigest(),
        'models_included':False,'credentials_included':False,'uploaded':False,'runtime_import_check':'passed','scope':'Local source-only preparation'},indent=2))
    print('Local source bundle saved:',len(inventory)+1,'entries. Nothing uploaded.')
if __name__=='__main__':main()
