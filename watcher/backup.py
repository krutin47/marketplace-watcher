"""Copy a live SQLite database using SQLite's consistent backup API."""
import argparse
from contextlib import closing
from pathlib import Path
import sqlite3


def backup(source: Path, destination: Path):
    if destination.exists():
        raise FileExistsError(destination)
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as live:
        destination.touch(mode=0o600, exist_ok=False)
        try:
            with closing(sqlite3.connect(destination)) as snapshot:
                live.backup(snapshot)
                if snapshot.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeError("Snapshot integrity check failed")
        except BaseException:
            destination.unlink()
            raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    backup(args.source, args.destination)
