"""Shrink the downloaded datasets into small map images for the web visualizer.

Every map frame becomes an 8-bit grey image on a global lat/lon grid
(row 0 = 90N, column 0 = 180W). Pixel 0 = no data (land, ice, no coverage);
1..255 = the value, packed with the stack's encoding (decode() in viz/web/index.html
undoes it). Frames of one stack are tiled into a few PNG "atlas" files.

Output: viz/web/data/<part>.json per dataset group, PNGs, and manifest.json (all parts merged).

Usage:
    .venv/Scripts/python -I scripts/build_viz.py              # everything
    .venv/Scripts/python -I scripts/build_viz.py glorys       # one part: woa oisst glorys chl gebco
"""
import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import xarray as xr
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "viz" / "web" / "data"
PART_ORDER = ["oisst", "glorys", "chl", "woa", "gebco"]
MAX_PX = 8_000_000  # pixels per atlas image, keeps browser decoding cheap
MAX_W = 8192

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


# ---------- encoding ----------

def encode(a, enc):
    """Float grid -> uint8 (0 = no data). Values outside [min, max] clip to 1 or 255."""
    a = np.asarray(a, dtype=np.float64)
    t, lo, hi = enc["type"], enc["min"], enc["max"]
    with np.errstate(invalid="ignore", divide="ignore"):
        if t == "linear":
            k = 1 + np.rint(np.clip((a - lo) / (hi - lo), 0, 1) * 254)
        elif t == "log":
            u = (np.log10(np.maximum(a, 1e-12)) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))
            k = 1 + np.rint(np.clip(u, 0, 1) * 254)
        elif t == "ssqrt":  # signed square root, fine steps near zero (currents)
            u = 0.5 + 0.5 * np.sign(a) * np.sqrt(np.minimum(np.abs(a) / hi, 1))
            k = 1 + np.rint(u * 254)
        elif t == "elev":  # ocean -> 1..200 (sqrt depth), land -> 200..255 (sqrt height)
            ocean = 1 + np.rint((1 - np.sqrt(np.clip(-a, 0, -lo) / -lo)) * 199)
            land = 200 + np.rint(np.sqrt(np.clip(a, 0, hi) / hi) * 55)
            k = np.where(a <= 0, ocean, land)
        else:
            raise ValueError(t)
    k[~np.isfinite(a)] = 0
    return k.astype(np.uint8)


# ---------- regridding ----------

class Binner:
    """Average a lat/lon grid into cells of `res` degrees, north-up, lon -180..180."""

    def __init__(self, lat, lon, res):
        self.h, self.w = round(180 / res), round(360 / res)
        lat = np.asarray(lat, dtype=np.float64)
        lon = (np.asarray(lon, dtype=np.float64) + 180) % 360 - 180
        r = np.clip(np.floor((90 - lat) / res).astype(np.int64), 0, self.h - 1)
        c = np.clip(np.floor((lon + 180) / res).astype(np.int64), 0, self.w - 1)
        self.idx = (r[:, None] * self.w + c[None, :]).ravel().astype(np.int32)
        self.n = self.h * self.w

    def __call__(self, a, geometric=False):
        a = np.asarray(a, dtype=np.float64).ravel()
        if geometric:
            with np.errstate(invalid="ignore", divide="ignore"):
                a = np.where(a > 0, np.log(a), np.nan)
        ok = np.isfinite(a)
        s = np.bincount(self.idx[ok], a[ok], self.n)
        n = np.bincount(self.idx[ok], minlength=self.n)
        with np.errstate(invalid="ignore", divide="ignore"):
            m = np.where(n > 0, s / np.maximum(n, 1), np.nan)
        if geometric:
            m = np.exp(m)
        return m.reshape(self.h, self.w)


# ---------- atlas writer ----------

class Stack:
    """Frames that share one encoding (one variable at one depth)."""

    def __init__(self, sid, enc):
        self.sid, self.enc, self.frames = sid, enc, []

    def add(self, label, grid):
        self.frames.append((label, encode(grid, self.enc)))

    def write(self):
        h, w = self.frames[0][1].shape
        per = max(1, min(len(self.frames), MAX_PX // (w * h)))
        cols = max(1, min(per, MAX_W // w))
        out = []
        for a, start in enumerate(range(0, len(self.frames), per)):
            chunk = self.frames[start:start + per]
            rows = math.ceil(len(chunk) / cols)
            img = np.zeros((rows * h, min(cols, len(chunk)) * w), np.uint8)
            fname = f"{self.sid}_{a}.png"
            for i, (label, k) in enumerate(chunk):
                y, x = (i // cols) * h, (i % cols) * w
                img[y:y + h, x:x + w] = k
                out.append({"label": label, "file": fname, "x": x, "y": y})
            Image.fromarray(img, "L").save(OUT / fname, optimize=True)
        print(f"  {self.sid}: {len(self.frames)} frames, {w}x{h}")
        return {"w": w, "h": h, "enc": self.enc, "frames": out}


def write_part(name, layers, stacks):
    data = {"layers": layers, "stacks": {s.sid: s.write() for s in stacks if s.frames}}
    (OUT / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ---------- datasets ----------

WOA_PERIODS = ["Annual"] + MONTHS + ["Jan–Mar", "Apr–Jun", "Jul–Sep", "Oct–Dec"]
WOA_DEPTHS = [0, 50, 100, 200, 500, 1000, 1500, 2000, 3000, 4000, 5000]


def build_woa():
    specs = [
        ("t", "temperature", "decav91C0", "Temperature normal", "°C", "thermal",
         {"type": "linear", "min": -2, "max": 34},
         "1991–2020 average", "Median standard error 0.22 °C at the surface, 0.02 °C at 1000 m"),
        ("s", "salinity", "decav91C0", "Salinity normal", "", "haline",
         {"type": "linear", "min": 30, "max": 39},
         "1991–2020 average", "Median standard error 0.03 at the surface, 0.002 at 1000 m"),
        ("o", "oxygen", "all", "Dissolved oxygen", "µmol/kg", "oxygen",
         {"type": "linear", "min": 0, "max": 420},
         "Average of all years with measurements", "Median standard error 2.2 µmol/kg at the surface, 1.2 at 1000 m"),
    ]
    layers, stacks, binner = [], [], None
    for v, folder, period, title, units, cmap, enc, when, acc in specs:
        by_depth = {d: Stack(f"woa_{v}_{d}", enc) for d in WOA_DEPTHS}
        for tt in range(17):
            f = RAW / "woa23" / folder / "1.00" / f"woa23_{period}_{v}{tt:02d}_01.nc"
            with xr.open_dataset(f, decode_times=False) as ds:
                a = ds[f"{v}_an"].isel(time=0)
                binner = binner or Binner(ds.lat.values, ds.lon.values, 1.0)
                have = set(np.round(a.depth.values).astype(int))
                for d in WOA_DEPTHS:
                    if d in have:
                        by_depth[d].add(WOA_PERIODS[tt], binner(a.sel(depth=d).values))
        stacks += by_depth.values()
        layers.append({
            "id": f"woa_{v}", "group": "Long-term normals", "title": title, "units": units,
            "cmap": cmap, "kind": "scalar", "time": "climatology",
            "source": "NOAA NCEI · World Ocean Atlas 2023", "res": "1°",
            "period": when, "accuracy": acc,
            "depths": [{"label": f"{d:,} m", "stack": f"woa_{v}_{d}"} for d in WOA_DEPTHS],
        })
    write_part("woa", layers, stacks)


def build_oisst():
    files = sorted((RAW / "oisst_v2.1").glob("sst.day.mean.*.nc"))
    stack = Stack("oisst_sst", {"type": "linear", "min": -2, "max": 34})
    with xr.open_mfdataset(files, combine="by_coords") as ds:
        sst = ds.sst
        binner = Binner(ds.lat.values, ds.lon.values, 0.5)
        times = ds.time.values.astype("datetime64[D]")
        for g in range(0, len(times), 7):
            block = sst.isel(time=slice(g, g + 7))
            a = block.mean("time", skipna=True).values
            t0, t1 = times[g], times[min(g + 6, len(times) - 1)]
            stack.add(f"{t0} → {str(t1)[5:]}", binner(a))
    first, last = times[0], times[-1]
    write_part("oisst", [{
        "id": "oisst", "group": "Recent ocean", "title": "Sea surface temperature", "units": "°C",
        "cmap": "thermal", "kind": "scalar", "time": "dates",
        "source": "NOAA PSL · OISST v2.1", "res": "0.5° (from 0.25°), weekly mean of daily data",
        "period": f"{first} → {last}, daily, shown as weekly means",
        "accuracy": "Typically 0.3–0.5 °C against buoys and Argo floats; worse near sea ice and coasts",
        "depths": [{"label": "Surface", "stack": "oisst_sst"}],
    }], [stack])


GLORYS_DEPTHS = [0, 10, 25, 50, 100, 200, 300, 500, 1000]


def build_glorys():
    files = sorted((RAW / "glorys12").glob("glorys12_monthly_*.nc"))  # skips *.nc.part
    encs = {
        "thetao": {"type": "linear", "min": -2, "max": 34},
        "so": {"type": "linear", "min": 30, "max": 39},
        "uo": {"type": "ssqrt", "min": -1.5, "max": 1.5},
        "vo": {"type": "ssqrt", "min": -1.5, "max": 1.5},
    }
    stacks = {(v, d): Stack(f"glorys_{v}_{d}", encs[v]) for v in encs for d in GLORYS_DEPTHS}
    months = []
    for f in files:
        print(" reading", f.name)
        with xr.open_dataset(f) as ds:
            binner = Binner(ds.latitude.values, ds.longitude.values, 0.5)
            for t, tv in enumerate(ds.time.values):
                label = str(tv)[:7]
                months.append(label)
                for v in encs:
                    for i, d in enumerate(GLORYS_DEPTHS):
                        a = ds[v].isel(time=t, depth=i).values
                        stacks[(v, d)].add(label, binner(a))
    common = {"group": "Recent ocean", "time": "dates", "source": "Copernicus Marine · GLORYS12V1 reanalysis",
              "res": "0.5° (from 1/12°), monthly mean",
              "period": f"{months[0]} → {months[-1]}, monthly" if months else "not downloaded yet"}

    def depths(v):
        return [{"label": f"{d:,} m" if d else "Surface", "stack": f"glorys_{v}_{d}"} for d in GLORYS_DEPTHS]

    layers = [
        {"id": "glorys_t", "title": "Temperature", "units": "°C", "cmap": "thermal", "kind": "scalar",
         "accuracy": "About 0.5 °C in the upper ocean (model constrained by observations)",
         "depths": depths("thetao"), **common},
        {"id": "glorys_s", "title": "Salinity", "units": "", "cmap": "haline", "kind": "scalar",
         "accuracy": "About 0.1–0.2 in the upper ocean", "depths": depths("so"), **common},
        {"id": "glorys_uv", "title": "Currents", "units": "m/s", "cmap": "speed", "kind": "vector",
         "accuracy": "Least reliable field: eddy positions can be off. 0.5° averaging smooths narrow jets",
         "depths": [{"label": f"{d:,} m" if d else "Surface", "u": f"glorys_uo_{d}", "v": f"glorys_vo_{d}"}
                    for d in GLORYS_DEPTHS], **common},
    ]
    write_part("glorys", layers, stacks.values())


def build_chl():
    files = sorted((RAW / "oceancolor_chl").glob("AQUA_MODIS.*.CHL.chlor_a.4km.nc"))
    stack = Stack("modis_chl", {"type": "log", "min": 0.01, "max": 100})
    mc = sorted((f for f in files if ".MC." in f.name), key=lambda f: f.name.split(".")[1][4:6])
    cu = [f for f in files if ".CU." in f.name]
    binner = None
    for f in mc + cu:
        start, end = f.name.split(".")[1].split("_")
        label = (f"{MONTHS[int(start[4:6]) - 1]} ({start[:4]}–{end[:4]})" if ".MC." in f.name
                 else f"Whole mission ({start[:4]}–{end[:4]})")
        with xr.open_dataset(f) as ds:
            binner = binner or Binner(ds.lat.values, ds.lon.values, 0.25)
            stack.add(label, binner(ds.chlor_a.values, geometric=True))
    write_part("chl", [{
        "id": "chl", "group": "Recent ocean", "title": "Chlorophyll", "units": "mg/m³", "cmap": "algae",
        "kind": "scalar", "time": "climatology", "source": "NASA OB.DAAC · MODIS-Aqua R2022.0",
        "res": "0.25° (from 4 km), geometric mean", "period": "Monthly averages over 2002–2026, plus whole mission",
        "accuracy": "About ±35% in open ocean; worse in coastal and murky water",
        "depths": [{"label": "Surface", "stack": "modis_chl"}],
    }], [stack])


def build_gebco():
    f = 24  # 15 arc-sec -> 0.1 degree
    strip = 240
    with xr.open_dataset(RAW / "gebco_2026" / "GEBCO_2026.nc") as ds, \
            xr.open_dataset(RAW / "gebco_2026" / "GEBCO_2026_TID.nc") as dt:
        H, W = ds.sizes["lat"], ds.sizes["lon"]
        elev = np.empty((H // f, W // f), np.float32)
        frac = np.empty((H // f, W // f), np.float32)
        for r0 in range(0, H, strip):
            e = ds.elevation[r0:r0 + strip].values.astype(np.float32)
            t = dt.tid[r0:r0 + strip].values
            n = e.shape[0] // f
            elev[r0 // f:r0 // f + n] = e.reshape(n, f, W // f, f).mean(axis=(1, 3))
            direct = ((t >= 10) & (t <= 17)).reshape(n, f, W // f, f).sum(axis=(1, 3))
            ocean = (t != 0).reshape(n, f, W // f, f).sum(axis=(1, 3))
            with np.errstate(invalid="ignore", divide="ignore"):
                frac[r0 // f:r0 // f + n] = np.where(ocean > 0, direct / ocean, np.nan)
            if r0 % (strip * 30) == 0:
                print(f"  gebco rows {r0}/{H}")
    elev, frac = elev[::-1], frac[::-1]  # file is south-first
    se = Stack("gebco_elev", {"type": "elev", "min": -11000, "max": 9000})
    se.add("GEBCO 2026", elev)
    st = Stack("gebco_tid", {"type": "linear", "min": 0, "max": 1})
    st.add("GEBCO 2026", frac)
    common = {"group": "Seafloor", "kind": "scalar", "time": "static", "source": "GEBCO 2026 grid",
              "res": "0.1° (from 15 arc-seconds)", "period": "Single snapshot (2026 release)"}
    layers = [
        {"id": "gebco", "title": "Seafloor depth", "units": "m", "cmap": "relief",
         "accuracy": "Exact where ships have mapped the seafloor (about a quarter of it); estimated elsewhere",
         "depths": [{"label": "Seafloor", "stack": "gebco_elev"}], **common},
        {"id": "gebco_tid", "title": "Seafloor mapping", "units": "%", "cmap": "mapped",
         "accuracy": "Share of each 0.1° cell measured directly by ship sonar, lidar or similar (TID codes 10–17)",
         "depths": [{"label": "Seafloor", "stack": "gebco_tid"}], **common},
    ]
    stacks = [se, st]

    # ETOPO 2022 (optional download): same 0.1° cells, plus GEBCO minus ETOPO as a cross-check
    etopo_file = RAW / "etopo_2022" / "ETOPO_2022_v1_60s_N90W180_surface.nc"
    if etopo_file.exists():
        with xr.open_dataset(etopo_file) as de:
            z = de.z.values.astype(np.float32, copy=False)  # 10800 x 21600, south-first, cell centres
        g = 6  # 60 arc-sec -> 0.1 degree
        ez = z.reshape(z.shape[0] // g, g, z.shape[1] // g, g).mean(axis=(1, 3))[::-1]
        sz = Stack("etopo_elev", {"type": "elev", "min": -11000, "max": 9000})
        sz.add("ETOPO 2022", ez)
        sd = Stack("gebco_minus_etopo", {"type": "ssqrt", "min": -500, "max": 500})
        sd.add("GEBCO 2026 − ETOPO 2022", elev - ez)
        stacks += [sz, sd]
        etopo = {"group": "Seafloor", "kind": "scalar", "time": "static",
                 "res": "0.1° (from 60 arc-seconds)", "period": "Single snapshot (2022 release)"}
        layers += [
            {"id": "etopo", "title": "Elevation, ETOPO", "units": "m", "cmap": "relief",
             "source": "NOAA NCEI · ETOPO 2022", **etopo,
             "accuracy": "Compiled from many of the same ship surveys as GEBCO, with satellite elevation models on land. "
                         "NOAA publishes separate error grids (not downloaded)",
             "depths": [{"label": "Seafloor", "stack": "etopo_elev"}]},
            {"id": "gebco_vs_etopo", "title": "GEBCO vs ETOPO", "units": "m", "cmap": "diff",
             "source": "GEBCO 2026 minus ETOPO 2022", **etopo, "period": "2026 release compared with 2022 release",
             "accuracy": "Where the two disagree, at least one is uncertain. Large differences often mark seafloor "
                         "mapped since 2022 or estimated areas. Colours stop at ±500 m",
             "depths": [{"label": "Seafloor", "stack": "gebco_minus_etopo"}]},
        ]
    write_part("gebco", layers, stacks)


BUILDERS = {"woa": build_woa, "oisst": build_oisst, "glorys": build_glorys, "chl": build_chl, "gebco": build_gebco}


def merge_manifest():
    layers, stacks = [], {}
    for name in PART_ORDER:
        p = OUT / f"{name}.json"
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            layers += d["layers"]
            stacks.update(d["stacks"])
    manifest = {"built": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "layers": layers, "stacks": stacks}
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    print(f"manifest: {len(layers)} layers, {len(stacks)} stacks")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("parts", nargs="*", help=" ".join(PART_ORDER))
    args = ap.parse_args()
    bad = set(args.parts) - set(BUILDERS)
    if bad:
        ap.error(f"unknown part(s): {' '.join(bad)}")
    OUT.mkdir(parents=True, exist_ok=True)
    for name in args.parts or PART_ORDER:
        print("building", name)
        BUILDERS[name]()
    merge_manifest()


if __name__ == "__main__":
    main()
