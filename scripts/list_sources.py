"""Phase 0 dry run: resolve every file each dataset needs, get its size, and
write the result to config/sources.yaml. Downloads nothing but file headers.

Usage:
    python scripts/list_sources.py            # all datasets
    python scripts/list_sources.py C1 S1      # only some
"""

from __future__ import annotations

import datetime as dt
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "sources.yaml"
UA = {"User-Agent": "ocean-data-catalog/0.1 (python-requests)"}
TIMEOUT = (30, 180)  # connect, read. NCEI and PSL listings can be slow.

WOA_ROOT = "https://www.ncei.noaa.gov/data/oceans/woa/WOA23/DATA"
WOA_PERIODS = [f"{i:02d}" for i in range(17)]  # 00 annual, 01-12 months, 13-16 seasons
OISST_ROOT = "https://downloads.psl.noaa.gov/Datasets/noaa.oisst.v2.highres"
OISST_FIRST_YEAR = 2024  # final scope: last ~2 years only (plan said 1991)
GEBCO_ROOT = "https://dap.ceda.ac.uk/bodc/gebco/global/gebco_2026"
CMR = "https://cmr.earthdata.nasa.gov/search/granules.json"
CHL_COLLECTION = "C3380709133-OB_CLOUD"  # MODISA_L3m_CHL, version 2022.0
ETOPO_URL = (
    "https://www.ngdc.noaa.gov/thredds/fileServer/global/ETOPO2022/60s/"
    "60s_surface_elev_netcdf/ETOPO_2022_v1_60s_N90W180_surface.nc"
)
GLORYS_DATASET = "cmems_mod_glo_phy_my_0.083deg_P1M-m"
GLORYS_VARS = ["thetao", "so", "uo", "vo"]
GLORYS_TARGET_DEPTHS = [0, 10, 25, 50, 100, 200, 300, 500, 1000]
GLORYS_MONTHS = 24  # final scope: most recent 2 years only (plan said 10)

session = requests.Session()
session.headers.update(UA)


# ---------------------------------------------------------------- helpers
def get(url: str, **kw) -> requests.Response:
    """GET with retries and backoff on network errors and 5xx (NCEI is flaky)."""
    for attempt in range(5):
        try:
            r = session.get(url, timeout=TIMEOUT, **kw)
            if r.status_code < 500:
                return r
        except requests.RequestException:
            if attempt == 4:
                raise
        time.sleep(5 * 2**attempt)
    r.raise_for_status()
    return r


def remote_size(url: str) -> int | None:
    """Exact byte size via a 1-byte range request (works on every host here)."""
    r = get(url, headers={"Range": "bytes=0-0"}, stream=True, allow_redirects=True)
    r.close()
    m = re.search(r"/(\d+)$", r.headers.get("Content-Range", ""))
    if r.status_code == 206 and m:
        return int(m.group(1))
    if r.status_code == 200 and r.headers.get("Content-Length"):
        return int(r.headers["Content-Length"])
    return None


def fill_sizes(files: list[dict]) -> None:
    """Size every file, at most 4 requests at a time per host."""
    by_host = defaultdict(list)
    for f in files:
        if f.get("bytes") is None:
            by_host[urlparse(f["url"]).netloc].append(f)
    def run(group):
        with ThreadPoolExecutor(4) as ex:
            for f, size in zip(group, ex.map(lambda f: remote_size(f["url"]), group)):
                f["bytes"], f["size_source"] = size, "http"
    with ThreadPoolExecutor(len(by_host) or 1) as ex:
        list(ex.map(run, by_host.values()))


def listing(url: str) -> list[str]:
    r = get(url)
    r.raise_for_status()
    return re.findall(r'href="([^"?/][^"]*)"', r.text)


def raw(*parts: str) -> str:
    return "/".join(("data/raw",) + parts)


# ---------------------------------------------------------------- datasets
def woa(dataset_id, var_dir, letter, period, res_dir, grid, title):
    url_dir = f"{WOA_ROOT}/{var_dir}/netcdf/{period}/{res_dir}/"
    on_server = set(listing(url_dir))
    files, missing = [], []
    for tt in WOA_PERIODS:
        name = f"woa23_{period}_{letter}{tt}_{grid}.nc"
        if name not in on_server:
            missing.append(name)
            continue
        files.append({"url": url_dir + name, "local_path": raw("woa23", var_dir, res_dir, name)})
    return {
        "id": dataset_id, "title": title, "source": "NOAA NCEI World Ocean Atlas 2023",
        "listing": url_dir, "auth": None, "expected_files": len(WOA_PERIODS),
        "missing_on_server": missing, "files": files,
    }


def gebco():
    files = [
        {"url": f"{GEBCO_ROOT}/ice_surface_elevation/netcdf/GEBCO_2026.zip?download=1",
         "local_path": raw("gebco_2026", "GEBCO_2026.zip"), "unzipped_gb_published": 7.0},
        {"url": f"{GEBCO_ROOT}/type_identifier_grid/netcdf/gebco_2026_tid.zip?download=1",
         "local_path": raw("gebco_2026", "gebco_2026_tid.zip"), "unzipped_gb_published": 3.5},
    ]
    return {
        "id": "C3+C4", "title": "GEBCO_2026 grid (elevation) + TID grid",
        "source": "GEBCO Compilation Group, hosted at CEDA", "auth": None,
        "expected_files": 2, "missing_on_server": [], "files": files,
        "notes": "Zips. Unzipped size adds ~10.5 GB. Ice-surface version of the grid "
                 "(GEBCO also publishes a sub-ice version; not pulled).",
    }


def oisst():
    names = set(listing(OISST_ROOT + "/"))
    this_year = dt.date.today().year
    files, missing = [], []
    for y in range(OISST_FIRST_YEAR, this_year + 1):
        name = f"sst.day.mean.{y}.nc"
        if name in names:
            files.append({"url": f"{OISST_ROOT}/{name}", "local_path": raw("oisst_v2.1", name)})
        else:
            missing.append(name)
    if "lsmask.oisst.nc" in names:
        files.append({"url": f"{OISST_ROOT}/lsmask.oisst.nc", "local_path": raw("oisst_v2.1", "lsmask.oisst.nc")})
    return {
        "id": "S1", "title": "OISST v2.1 daily SST, 0.25 deg", "source": "NOAA PSL",
        "listing": OISST_ROOT + "/", "auth": None, "expected_files": this_year - OISST_FIRST_YEAR + 2,
        "missing_on_server": missing, "files": files,
        "notes": f"{this_year} file is partial (still being updated daily).",
    }


def chlorophyll():
    """Newest version of each monthly climatology (MC) and of the mission
    climatology (CU, used as the annual climatology), from NASA CMR."""
    entries = []
    for pattern in ("*.L3m.MC.CHL.chlor_a.4km.nc", "*.L3m.CU.CHL.chlor_a.4km.nc"):
        page = 1
        while True:
            r = get(CMR, params={
                "collection_concept_id": CHL_COLLECTION, "producer_granule_id[]": pattern,
                "options[producer_granule_id][pattern]": "true",
                "page_size": 2000, "page_num": page})
            r.raise_for_status()
            batch = r.json()["feed"]["entry"]
            entries += batch
            if len(batch) < 2000:
                break
            page += 1

    best = {}  # key: 'MC-MM' or 'CU' -> (end_date, entry, url)
    for e in entries:
        name = e["producer_granule_id"]
        m = re.match(r"AQUA_MODIS\.(\d{8})_(\d{8})\.L3m\.(MC|CU)\.", name)
        url = next((l["href"] for l in e.get("links", [])
                    if l.get("rel", "").endswith("data#") and l["href"].endswith(name)
                    and l["href"].startswith("https://")), None)
        if not (m and url):
            continue
        key = "CU" if m.group(3) == "CU" else f"MC-{m.group(1)[4:6]}"
        if key not in best or m.group(2) > best[key][0]:
            best[key] = (m.group(2), e, url)

    files = []
    for key in sorted(best):
        _, e, url = best[key]
        files.append({"url": url, "local_path": raw("oceancolor_chl", e["producer_granule_id"]),
                      "bytes": int(float(e["granule_size"]) * 1024 * 1024),
                      "size_source": "CMR granule_size (approx.)"})
    return {
        "id": "S5", "title": "MODIS-Aqua L3m chlor_a 4 km: 12 monthly + mission climatology",
        "source": "NASA OB.DAAC via Earthdata Cloud", "auth": "earthdata (~/.netrc)",
        "expected_files": 13, "missing_on_server": [f"MC-{m:02d}" for m in range(1, 13) if f"MC-{m:02d}" not in best]
        + ([] if "CU" in best else ["CU"]), "files": files,
        "notes": "OB.DAAC has no separate 'annual climatology'; the CU (cumulative, "
                 "whole-mission) composite is used for it. Each month has several "
                 "yearly re-issues; the newest is picked.",
    }


def glorys():
    import copernicusmarine as cm  # describe() needs no login

    r = cm.describe(dataset_id=GLORYS_DATASET, disable_progress_bar=True)
    part = r.products[0].datasets[0].versions[0].parts[0]
    svc = next(s for s in part.services
               if getattr(s.service_name, "value", s.service_name) == "arco-geo-series")
    coords = {c.coordinate_id: c for c in next(v for v in svc.variables if v.short_name == "thetao").coordinates}
    depths = sorted(coords["depth"].values)
    picked = [min(depths, key=lambda d: abs(d - t)) for t in GLORYS_TARGET_DEPTHS]
    times = sorted(dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc) for t in coords["time"].values)
    window = times[-GLORYS_MONTHS:]
    lat, lon = coords["latitude"], coords["longitude"]
    nlat = round((lat.maximum_value - lat.minimum_value) / lat.step) + 1
    nlon = round((lon.maximum_value - lon.minimum_value) / lon.step) + 1

    files = []
    for year in sorted({t.year for t in window}):
        months = [t for t in window if t.year == year]
        n_bytes = nlat * nlon * 2 * len(GLORYS_VARS) * len(picked) * len(months)  # int16, as stored
        files.append({
            "url": f"copernicusmarine://{GLORYS_DATASET}",
            "local_path": raw("glorys12", f"glorys12_monthly_{year}.nc"),
            "subset": {"start": months[0].strftime("%Y-%m-%d"), "end": months[-1].strftime("%Y-%m-%d"),
                       "variables": GLORYS_VARS, "depths_m": [round(d, 3) for d in picked]},
            "bytes": n_bytes, "size_source": "estimate, uncompressed int16 (matches toolbox dry run)",
        })
    return {
        "id": "S2+S3", "title": "GLORYS12V1 monthly means: thetao, so, uo, vo",
        "source": "Copernicus Marine GLOBAL_MULTIYEAR_PHY_001_030", "dataset_id": GLORYS_DATASET,
        "auth": "copernicus marine", "expected_files": len(files), "missing_on_server": [],
        "grid": {"nlat": nlat, "nlon": nlon, "lat": [lat.minimum_value, lat.maximum_value],
                 "lon": [lon.minimum_value, lon.maximum_value]},
        "available_time": [times[0].strftime("%Y-%m"), times[-1].strftime("%Y-%m")],
        "files": files,
        "notes": "Sizes are uncompressed estimates; files are written compressed (~40% of this). "
                 "Network transfer is ~11x the uncompressed size (server chunks span many depths).",
    }


def etopo():
    name = ETOPO_URL.rsplit("/", 1)[1]
    return {
        "id": "O1", "title": "ETOPO 2022 60 arc-sec surface elevation (optional)",
        "source": "NOAA NCEI", "auth": None, "optional": True, "expected_files": 1,
        "missing_on_server": [],
        "files": [{"url": ETOPO_URL, "local_path": raw("etopo_2022", name)}],
    }


DATASETS = {
    "C1": lambda: woa("C1", "temperature", "t", "decav91C0", "1.00", "01", "WOA23 temperature, 1 deg"),
    "C2": lambda: woa("C2", "salinity", "s", "decav91C0", "1.00", "01", "WOA23 salinity, 1 deg"),
    "C3+C4": gebco,
    "S4": lambda: woa("S4", "oxygen", "o", "all", "1.00", "01", "WOA23 dissolved oxygen, 1 deg"),
    "S2+S3": glorys,
    "S1": oisst,
    "S5": chlorophyll,
    "O1": etopo,
}
PHASE = {"C1": 1, "C2": 1, "C3+C4": 2, "S4": 4, "S2+S3": 5, "S1": 6, "S5": 7, "O1": 8}


def main(wanted: list[str]) -> None:
    previous = {}
    if CONFIG.exists():
        previous = {d["id"]: d for d in yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["datasets"]}
    out = []
    for key, build in DATASETS.items():
        if wanted and key not in wanted:
            continue
        print(f"resolving {key} ...", flush=True)
        try:
            ds = build()
            fill_sizes(ds["files"])
        except Exception as exc:  # report and keep going; one bad host shouldn't stop the dry run
            err = f"{type(exc).__name__}: {exc}"
            if previous.get(key, {}).get("files"):  # keep the last good list rather than wiping it
                print(f"  {key} failed ({err[:80]}); keeping previous file list", flush=True)
                ds = previous[key]
            else:
                ds = {"id": key, "error": err, "files": []}
        ds["phase"] = PHASE[key]
        ds["file_count"] = len(ds["files"])
        ds["total_bytes"] = sum(f.get("bytes") or 0 for f in ds["files"])
        ds["unsized_files"] = sum(f.get("bytes") is None for f in ds["files"])
        out.append(ds)

    if wanted and CONFIG.exists():  # partial re-run: keep the other datasets
        fresh = {d["id"] for d in out}
        kept = [d for d in yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["datasets"] if d["id"] not in fresh]
        out = kept + out
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    doc = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "datasets": out}
    CONFIG.write_text(yaml.safe_dump(doc, sort_keys=False, width=120), encoding="utf-8")

    print(f"\nwrote {CONFIG.relative_to(ROOT)}\n")
    print(f"{'phase':>5}  {'dataset':<7} {'files':>5} {'GB':>8}  notes")
    for ds in sorted(out, key=lambda d: d["phase"]):
        flag = []
        if ds.get("error"):
            flag.append("ERROR " + ds["error"][:70])
        if ds.get("missing_on_server"):
            flag.append(f"missing: {ds['missing_on_server']}")
        if ds.get("unsized_files"):
            flag.append(f"{ds['unsized_files']} unsized")
        if ds["files"] and "estimate" in ds["files"][0].get("size_source", ""):
            flag.append("estimate")
        if ds.get("optional"):
            flag.append("optional")
        print(f"{ds['phase']:>5}  {ds['id']:<7} {ds['file_count']:>5} {ds['total_bytes'] / 1e9:>8.2f}  {'; '.join(flag)}")
    total = sum(d["total_bytes"] for d in out)
    print(f"{'':>5}  {'TOTAL':<7} {sum(d['file_count'] for d in out):>5} {total / 1e9:>8.2f}")


if __name__ == "__main__":
    main(sys.argv[1:])
