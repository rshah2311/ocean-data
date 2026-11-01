"""catalog/manifest.csv: one row per file, keyed by local_path.

download.py fills the download columns, verify.py fills the rest.
"""

from __future__ import annotations

import csv
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "catalog" / "manifest.csv"
COLUMNS = [
    "dataset_id", "source", "url", "local_path", "bytes", "sha256", "downloaded_at",
    "variables", "dims", "lat_range", "lon_range", "depth_range", "time_range",
    "units", "status", "notes",
]
_lock = threading.Lock()


def read() -> dict[str, dict]:
    if not MANIFEST.exists():
        return {}
    with MANIFEST.open(newline="", encoding="utf-8") as f:
        return {r["local_path"]: r for r in csv.DictReader(f)}


def _write(rows: dict[str, dict]) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for key in sorted(rows, key=lambda k: (rows[k]["dataset_id"], k)):
            w.writerow({c: rows[key].get(c, "") for c in COLUMNS})
    tmp.replace(MANIFEST)


def upsert(row: dict, keep_existing: tuple[str, ...] = ()) -> None:
    """Merge `row` into the manifest. Columns in keep_existing are not overwritten
    if they already have a value."""
    with _lock:
        rows = read()
        old = rows.get(row["local_path"], {})
        new = {**old, **{k: v for k, v in row.items() if not (k in keep_existing and old.get(k))}}
        rows[row["local_path"]] = new
        _write(rows)
