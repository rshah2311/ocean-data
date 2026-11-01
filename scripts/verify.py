"""Open every downloaded file, record what is in it, and run the acceptance
checks from the plan. Results go into catalog/manifest.csv (status ok/FAIL).

Usage:
    python scripts/verify.py              # every file in the manifest
    python scripts/verify.py C1 C2        # only these datasets
"""

from __future__ import annotations

import re
import sys
import warnings
from pathlib import Path

import numpy as np
import xarray as xr

sys.path.insert(0, str(Path(__file__).resolve().parent))
import manifest  # noqa: E402

ROOT = manifest.ROOT
warnings.filterwarnings("ignore", category=RuntimeWarning)

WOA_STATS = ["an", "mn", "dd", "sd", "se", "oa", "ma", "gp"]
MAIN_VAR = {"C1": "t_an", "C2": "s_an", "S4": "o_an",
            "S1": "sst", "S5": "chlor_a", "O1": "z", "S2+S3": "thetao"}
WOA_GRID = {"01": (180, 360), "04": (720, 1440)}  # (lat, lon)
GEBCO_SHAPE = (43200, 86400)


def glorys_subset(path: Path) -> dict:
    """The subset (time window, depths) sources.yaml lists for this GLORYS file."""
    import yaml
    doc = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8"))
    ds = next(d for d in doc["datasets"] if d["id"] == "S2+S3")
    rel = path.relative_to(ROOT).as_posix()
    return next(f["subset"] for f in ds["files"] if f["local_path"] == rel)


def find(ds: xr.Dataset, *names: str) -> str | None:
    for n in names:
        if n in ds.variables:
            return n
    return None


def rng(values, fmt="{:.4g}") -> str:
    v = np.asarray(values)
    if v.size == 0:
        return ""
    return f"{fmt.format(np.nanmin(v))} .. {fmt.format(np.nanmax(v))}"


def time_range(ds: xr.Dataset) -> str:
    if "time" not in ds.variables:
        return ""
    t = ds["time"]
    try:
        from xarray.coding.times import decode_cf_datetime
        dec = np.atleast_1d(decode_cf_datetime(t.values, t.attrs["units"], t.attrs.get("calendar", "standard")))
        return f"{str(dec.min())[:10]} .. {str(dec.max())[:10]} (n={dec.size})"
    except Exception:
        return f"{rng(t.values)} {t.attrs.get('units', '')} (n={t.size})"


def sample(da: xr.DataArray, target: int = 2_000_000) -> np.ndarray:
    """A strided sample, so a 7 GB grid doesn't have to be read in full."""
    step = max(1, int((da.size / target) ** (1 / max(da.ndim, 1))))
    return da.isel({d: slice(None, None, step) for d in da.dims}).values


def describe(ds: xr.Dataset, row: dict, main: str | None) -> None:
    lat = find(ds, "lat", "latitude")
    lon = find(ds, "lon", "longitude")
    dep = find(ds, "depth")
    row["variables"] = " ".join(sorted(v for v in ds.data_vars))
    row["dims"] = " ".join(f"{k}={v}" for k, v in ds.sizes.items())
    row["lat_range"] = rng(ds[lat].values) if lat else ""
    if lon:
        lo = ds[lon].values
        row["lon_range"] = f"{rng(lo)} ({'0-360' if np.nanmax(lo) > 180 else '-180-180'})"
    row["depth_range"] = f"{rng(ds[dep].values)} m (n={ds[dep].size})" if dep else ""
    row["time_range"] = time_range(ds)
    if main and main in ds:
        row["units"] = f"{main}: {ds[main].attrs.get('units', '?')}"


def check(dsid: str, path: Path, ds: xr.Dataset, row: dict) -> list[str]:
    """Return a list of problems (empty = ok). Adds informational notes to row."""
    problems, notes = [], []
    main = MAIN_VAR.get(dsid)
    name = path.name

    if dsid in ("C1", "C2", "S4"):
        m = re.match(r"woa23_\w+?_([tso])(\d\d)_(\d\d)\.nc", name)
        letter, period, grid = m.groups()
        # _ma (period minus annual mean) only exists in monthly/seasonal files
        want = {f"{letter}_{s}" for s in WOA_STATS if not (s == "ma" and period == "00")}
        if missing := want - set(ds.data_vars):
            problems.append(f"missing vars {sorted(missing)}")
        # 57 levels = 0-1500 m, 102 = 0-5500 m. Oxygen seasonal files stop at 1500 m;
        # temperature/salinity seasonal files go to 5500 m.
        deep = period == "00" or (letter in "ts" and int(period) >= 13)
        ndep = 102 if deep else 57
        if ds.sizes.get("depth") != ndep:
            problems.append(f"depth={ds.sizes.get('depth')} expected {ndep}")
        if (ds.sizes.get("lat"), ds.sizes.get("lon")) != WOA_GRID[grid]:
            problems.append(f"grid {ds.sizes.get('lat')}x{ds.sizes.get('lon')} expected {WOA_GRID[grid]}")
        main = f"{letter}_an"
        if letter == "t" and period == "00":  # sanity checks from the plan (flag only)
            trop = ds[main].isel(time=0).sel(lat=slice(-10, 10))
            sst = float(trop.sel(depth=0).mean())
            t1000 = float(trop.sel(depth=1000).mean())
            notes.append(f"sanity: tropical (10S-10N) mean t_an at 0 m = {sst:.1f} C; at 1000 m = {t1000:.1f} C")
            if not 26 <= sst <= 30:
                notes.append("FLAG tropical surface temp outside 26-30 C")
            if not 4 <= t1000 <= 7:
                notes.append("FLAG 1000 m temp outside 4-7 C")

    elif dsid == "C3+C4":
        var = "elevation" if "elevation" in ds else "tid" if "tid" in ds else None
        main = var
        if var is None:
            problems.append("neither elevation nor tid present")
        elif ds[var].shape != GEBCO_SHAPE:
            problems.append(f"shape {ds[var].shape} expected {GEBCO_SHAPE}")
        if var == "elevation":
            box = ds["elevation"].sel(lat=slice(11.2, 11.5), lon=slice(142.0, 142.9))
            deep = int(box.min())
            notes.append(f"Challenger Deep min (11.2-11.5N, 142.0-142.9E) = {deep} m")
            if not -11100 <= deep <= -10700:
                problems.append(f"Challenger Deep {deep} m not ~ -10,900")
            s = sample(ds["elevation"])
            notes.append(f"sample: {np.mean(s < 0) * 100:.0f}% of cells below sea level, range {s.min()}..{s.max()} m")
            ocean = float(ds["elevation"].sel(lat=0, lon=-140, method="nearest"))  # mid-Pacific
            land = float(ds["elevation"].sel(lat=-25, lon=134, method="nearest"))  # central Australia
            notes.append(f"sanity: mid-Pacific {ocean:.0f} m, central Australia {land:.0f} m")
            if not (ocean < 0 < land):
                notes.append("FLAG sign convention unexpected")

    elif dsid == "S2+S3":
        sub = glorys_subset(path)
        want_months = (int(sub["end"][:4]) - int(sub["start"][:4])) * 12 + int(sub["end"][5:7]) - int(sub["start"][5:7]) + 1
        if missing := {"thetao", "so", "uo", "vo"} - set(ds.data_vars):
            problems.append(f"missing vars {sorted(missing)}")
        if ds.sizes.get("time") != want_months:
            problems.append(f"time={ds.sizes.get('time')} expected {want_months}")
        if ds.sizes.get("depth") != len(sub["depths_m"]):
            problems.append(f"depth={ds.sizes.get('depth')} expected {len(sub['depths_m'])}")
        if (ds.sizes.get("latitude"), ds.sizes.get("longitude")) != (2041, 4320):
            problems.append(f"grid {ds.sizes.get('latitude')}x{ds.sizes.get('longitude')} expected 2041x4320")
        th = ds["thetao"].isel(time=0).sel(latitude=slice(-10, 10))
        top, bottom = float(th.isel(depth=0).mean()), float(th.isel(depth=-1).mean())
        notes.append(f"sanity: tropical thetao first month {top:.1f} C at {float(ds.depth[0]):.1f} m, "
                     f"{bottom:.1f} C at {float(ds.depth[-1]):.0f} m")
        if not 26 <= top <= 30:
            notes.append("FLAG tropical surface temp outside 26-30 C")
        if not 4 <= bottom <= 7:
            notes.append("FLAG ~1000 m temp outside 4-7 C")

    elif dsid == "S1":
        y = re.search(r"\.(\d{4})\.nc$", name)
        if y:
            year = int(y.group(1))
            ndays = 366 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 365
            n = ds.sizes.get("time", 0)
            if n != ndays:
                if year == np.datetime64("today", "Y").astype(int) + 1970:
                    notes.append(f"current year, partial: {n} of {ndays} days")
                else:
                    problems.append(f"{n} days, expected {ndays}")
        notes.append("longitude is 0-360 (kept as is)")

    if main and main in ds:
        s = sample(ds[main])
        if np.issubdtype(s.dtype, np.floating) and np.isnan(s).all():
            problems.append(f"{main} sample is all NaN")
    describe(ds, row, main)
    row["notes"] = "; ".join(problems + notes)
    return problems


def main(wanted: list[str]) -> None:
    rows = manifest.read()
    n_ok = n_fail = 0
    for key, row in rows.items():
        dsid = row["dataset_id"]
        if wanted and dsid not in wanted:
            continue
        path = ROOT / key
        row = dict(row)
        if not path.exists():
            row["status"], row["notes"] = "FAIL", "file missing"
        elif path.suffix == ".zip":
            row["status"], row["notes"] = "ok", "zip kept; contents verified in the unzipped rows"
        elif path.suffix != ".nc":
            row["status"], row["notes"] = "ok", "documentation from the zip (not data)"
        else:
            try:
                with xr.open_dataset(path, decode_times=False) as ds:
                    problems = check(dsid, path, ds, row)
                row["status"] = "FAIL" if problems else "ok"
            except Exception as exc:
                row["status"], row["notes"] = "FAIL", f"could not open: {type(exc).__name__}: {exc}"
        manifest.upsert(row)
        if row["status"] == "ok":
            n_ok += 1
        else:
            n_fail += 1
        print(f"{row['status']:4}  {key}  {row.get('notes', '')[:150]}")
    print(f"\n{n_ok} ok, {n_fail} FAIL")
    if n_fail:
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
