"""Create an isolated backend environment only. Does not start services or fetch data."""
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]


def main():
    if sys.version_info[:2] != (3, 11):
        raise SystemExit("Python 3.11 is required; do not replace the reviewed ML environment")
    environment = ROOT / ".venv"
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not environment.exists():
        venv.EnvBuilder(with_pip=True).create(environment)
    if not python.is_file():
        raise SystemExit("Existing backend/.venv is incompatible; recreate it manually for this OS")
    subprocess.run([str(python), "-B", "-c",
                    "import sys; assert sys.version_info[:2] == (3, 11), 'Python 3.11 required'"], check=True)
    subprocess.run([str(python), "-B", "-m", "pip", "install", "--disable-pip-version-check",
                    "--no-cache-dir", "--only-binary=:all:", "--index-url", "https://pypi.org/simple",
                    "-r", str(ROOT / "requirements.txt")], check=True)
    subprocess.run([str(python), "-B", "-m", "pip", "check"], check=True)
    print("Backend venv ready. Transfer the reviewed model privately and configure secrets before startup.")


if __name__ == "__main__":
    main()
