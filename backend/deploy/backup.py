"""Consistent SQLite online backup, including committed WAL content. No overwrite."""
import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3


def backup(source: Path, destination: Path):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    # Use an existing private directory; never silently change its permissions.
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(destination)) as copy:
                original.backup(copy)
                if copy.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise RuntimeError("Backup integrity check failed")
    except BaseException:
        destination.unlink()  # Only the new, exclusively created backup belongs to this call.
        raise
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    backup(args.source, args.destination)
    print("Backup completed and integrity verified")


if __name__ == "__main__":
    main()
