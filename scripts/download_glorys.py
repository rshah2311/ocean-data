"""Download GLORYS12V1 monthly means (S2+S3) as listed in config/sources.yaml:
one file per calendar year, variables thetao/so/uo/vo, at the 9 listed depth levels.

Uses the Copernicus Marine toolbox (`copernicusmarine login` must have been run).
`subset` only takes a continuous depth range, so this opens the same server data
with `copernicusmarine.open_dataset`, picks the exact levels, and writes the year
with the same int16 packing the toolbox uses, plus zlib compression. Values are
not changed.

Resumable per year: a finished year is skipped; an interrupted year restarts.

Usage:
    python scripts/download_glorys.py                 # all years in sources.yaml
    python scripts/download_glorys.py 2025            # some years
    python scripts/download_glorys.py --test          # one month, one file in data/raw/glorys12/_test/
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

import copernicusmarine as cm
import dask
import yaml

# Server chunks are ~600 MB each (25 depths x 3 months). With one worker per CPU
# thread a 16 GB machine runs out of memory, so cap the parallel reads.
dask.config.set(scheduler="threads", num_workers=3)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from download import sha256  # noqa: E402
from manifest import upsert  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "sources.yaml"
KEEP_ENCODING = ("dtype", "scale_factor", "add_offset", "_FillValue", "units")


def write_year(dataset_id: str, sub: dict, dest: Path) -> None:
    depths = sub["depths_m"]
    ds = cm.open_dataset(
        dataset_id=dataset_id,
        variables=sub["variables"],
        start_datetime=sub["start"],
        end_datetime=sub["end"],
        minimum_depth=min(depths),  # listed depths are rounded down slightly, so this stays inside
        maximum_depth=max(depths) + 0.01,
    )
    ds = ds.sel(depth=depths, method="nearest")
    got = [round(float(d), 3) for d in ds["depth"].values]
    if got != [round(d, 3) for d in depths]:
        raise ValueError(f"depth levels {got} != requested {depths}")

    encoding = {}
    for name, var in ds.data_vars.items():
        enc = {k: v for k, v in var.encoding.items() if k in KEEP_ENCODING}
        enc.update(zlib=True, complevel=1, chunksizes=(1, 1, ds.sizes["latitude"], ds.sizes["longitude"]))
        encoding[name] = enc
    for name in ds.data_vars:
        ds[name].encoding = {}
    ds.attrs["history_download"] = (
        f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H:%MZ} copernicusmarine.open_dataset "
        f"{dataset_id}, depths {depths}, {sub['start']}..{sub['end']}; values unchanged"
    )

    part = dest.with_name(dest.name + ".part")
    part.unlink(missing_ok=True)
    ds.to_netcdf(part, encoding=encoding, engine="netcdf4")
    part.replace(dest)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("years", nargs="*", type=int)
    ap.add_argument("--test", action="store_true", help="one month into data/raw/glorys12/_test/")
    args = ap.parse_args()

    doc = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    ds_cfg = next(d for d in doc["datasets"] if d["id"] == "S2+S3")
    dataset_id = ds_cfg["dataset_id"]
    files = ds_cfg["files"]

    if args.test:
        f = files[-1]
        sub = {**f["subset"], "end": f["subset"]["start"]}
        dest = ROOT / "data/raw/glorys12/_test" / f"glorys12_test_{sub['start'][:7]}.nc"
        dest.parent.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        write_year(dataset_id, sub, dest)
        print(f"test file {dest.relative_to(ROOT)}: {dest.stat().st_size / 1e6:.0f} MB in {time.time() - t0:.0f}s")
        return

    for f in files:
        year = int(f["local_path"][-7:-3])
        if args.years and year not in args.years:
            continue
        dest = ROOT / f["local_path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            print(f"skip {dest.name}: already there", flush=True)
            continue
        print(f"get {dest.name}: {f['subset']['start']} .. {f['subset']['end']}", flush=True)
        t0 = time.time()
        for attempt in range(1, 4):
            try:
                write_year(dataset_id, f["subset"], dest)
                break
            except Exception as exc:
                print(f"  attempt {attempt} failed: {type(exc).__name__}: {exc}", flush=True)
                if attempt == 3:
                    raise
                time.sleep(60 * attempt)
        print(f"ok  {dest.name}: {dest.stat().st_size / 1e9:.2f} GB in {(time.time() - t0) / 60:.0f} min", flush=True)
        upsert({
            "dataset_id": ds_cfg["id"], "source": ds_cfg["source"],
            "url": f"copernicusmarine://{dataset_id}?vars={','.join(f['subset']['variables'])}"
                   f"&time={f['subset']['start']}..{f['subset']['end']}"
                   f"&depths={','.join(str(d) for d in f['subset']['depths_m'])}",
            "local_path": f["local_path"], "bytes": dest.stat().st_size, "sha256": sha256(dest),
            "downloaded_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        })


if __name__ == "__main__":
    main()
