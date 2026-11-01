"""Download the files listed in config/sources.yaml.

- Resumable: writes <file>.part and continues it with HTTP Range.
- Retries with backoff (NCEI in particular drops out for minutes at a time).
- Skips files already on disk with the expected size.
- Records bytes, SHA-256 and time in catalog/manifest.csv.
- Unzips .zip files next to the zip (zip is kept).
- At most 4 parallel downloads per host.

Usage:
    python scripts/download.py C1 C2                # whole datasets
    python scripts/download.py C1 --only t00_01     # files whose name contains a string
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import sys
import threading
import time
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from manifest import upsert  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "sources.yaml"
CHUNK = 4 * 1024 * 1024
MAX_TRIES = 10
UA = {"User-Agent": "ocean-data-catalog/0.1 (python-requests)"}
print_lock = threading.Lock()


def log(msg: str) -> None:
    with print_lock:
        print(msg, flush=True)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(CHUNK):
            h.update(block)
    return h.hexdigest()


def fetch(url: str, dest: Path, expected: int | None, session: requests.Session) -> None:
    part = dest.with_name(dest.name + ".part")
    for attempt in range(1, MAX_TRIES + 1):
        have = part.stat().st_size if part.exists() else 0
        if expected and have == expected:
            break
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            with session.get(url, headers=headers, stream=True, timeout=(30, 300)) as r:
                if r.status_code == 416 and expected and have == expected:
                    break
                if r.status_code >= 500 or r.status_code == 429:
                    raise requests.HTTPError(f"{r.status_code} {r.reason}")
                r.raise_for_status()
                if have and r.status_code != 206:  # server ignored Range: start over
                    have = 0
                with part.open("ab" if have else "wb") as f:
                    for block in r.iter_content(CHUNK):
                        f.write(block)
            size = part.stat().st_size
            if expected is None or size == expected:
                break
            raise IOError(f"size {size} != expected {expected}")
        except (requests.RequestException, IOError) as exc:
            wait = min(300, 10 * 2 ** (attempt - 1))
            log(f"  retry {attempt}/{MAX_TRIES} {dest.name}: {exc} (waiting {wait}s)")
            if attempt == MAX_TRIES:
                raise
            time.sleep(wait)
    part.replace(dest)


def unzip(zpath: Path) -> list[Path]:
    out = []
    target = zpath.parent.resolve()
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            dest = (target / info.filename).resolve()
            if target not in dest.parents:  # refuse paths that escape the folder
                raise ValueError(f"unsafe path in zip: {info.filename}")
            if info.is_dir():
                continue
            if dest.exists() and dest.stat().st_size == info.file_size:
                out.append(dest)
                continue
            log(f"  unzipping {info.filename} ({info.file_size / 1e9:.2f} GB)")
            z.extract(info, target)
            out.append(dest)
    return out


def handle(ds: dict, f: dict, session: requests.Session) -> str:
    dest = ROOT / f["local_path"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    expected = f.get("bytes") if f.get("size_source") == "http" else None
    row = {"dataset_id": ds["id"], "source": ds["source"], "url": f["url"], "local_path": f["local_path"]}

    if dest.exists() and (expected is None or dest.stat().st_size == expected):
        status = "present"
    else:
        log(f"get {dest.name} ({(expected or 0) / 1e6:.0f} MB)")
        t0 = time.time()
        fetch(f["url"], dest, expected, session)
        log(f"ok  {dest.name} in {time.time() - t0:.0f}s")
        status = "downloaded"
        row["downloaded_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    row["bytes"] = dest.stat().st_size
    row["sha256"] = sha256(dest)
    upsert(row, keep_existing=("downloaded_at",) if status == "present" else ())

    if dest.suffix == ".zip":
        for member in unzip(dest):
            rel = member.relative_to(ROOT).as_posix()
            upsert({"dataset_id": ds["id"], "source": ds["source"],
                    "url": f"{f['url']} (unzipped from {dest.name})", "local_path": rel,
                    "bytes": member.stat().st_size, "sha256": sha256(member),
                    "downloaded_at": row.get("downloaded_at", "")},
                   keep_existing=("downloaded_at",) if status == "present" else ())
    return status


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("datasets", nargs="+", help="dataset ids from sources.yaml, e.g. C1 C2")
    ap.add_argument("--only", help="only files whose name contains this text")
    args = ap.parse_args()

    doc = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    jobs = []
    for ds in doc["datasets"]:
        if ds["id"] not in args.datasets:
            continue
        if ds.get("auth") == "copernicus marine":
            sys.exit(f"{ds['id']} uses the Copernicus toolbox, not this script")
        for f in ds["files"]:
            if not args.only or args.only in f["local_path"]:
                jobs.append((ds, f))
    if not jobs:
        sys.exit("nothing matched")

    by_host = defaultdict(list)
    for ds, f in jobs:
        by_host[urlparse(f["url"]).netloc].append((ds, f))

    results = defaultdict(int)
    failures = []

    def run_host(items):
        session = requests.Session()
        session.headers.update(UA)
        with ThreadPoolExecutor(4) as ex:
            futs = {ex.submit(handle, ds, f, session): f for ds, f in items}
            for fut, f in futs.items():
                try:
                    results[fut.result()] += 1
                except Exception as exc:
                    failures.append((f["local_path"], exc))
                    log(f"FAIL {f['local_path']}: {exc}")

    with ThreadPoolExecutor(len(by_host)) as ex:
        list(ex.map(run_host, by_host.values()))

    log(f"\n{dict(results)}; {len(failures)} failed")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
