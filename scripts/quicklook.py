"""One sanity-check map per dataset, written to catalog/quicklooks/<id>.png.
Datasets that haven't been downloaded yet are skipped.

Usage:
    python scripts/quicklook.py            # all available
    python scripts/quicklook.py C1 C3      # some
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.colors as mcolors  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import xarray as xr  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "catalog" / "quicklooks"


def first(pattern: str) -> Path | None:
    hits = sorted(RAW.glob(pattern))
    return hits[0] if hits else None


def save(fig, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / f"{name}.png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote catalog/quicklooks/{name}.png")


def panel(ax, da: xr.DataArray, title: str, src: Path, **kw):
    im = da.plot.pcolormesh(ax=ax, add_colorbar=False, rasterized=True, **kw)
    ax.set_title(title, fontsize=10)
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.text(0, -0.08, src.name, transform=ax.transAxes, fontsize=7, color="0.4")
    return im


def single(name, da, title, src, label, **kw):
    fig, ax = plt.subplots(figsize=(10, 5))
    im = panel(ax, da, title, src, **kw)
    fig.colorbar(im, ax=ax, label=label, shrink=0.8)
    save(fig, name)


def woa(name, pattern, var, depth, title, label, cmap, **kw):
    f = first(pattern)
    if not f:
        return print(f"skip {name}: no file")
    with xr.open_dataset(f, decode_times=False) as ds:
        da = ds[var].isel(time=0).sel(depth=depth)
        single(name, da, title, f, f"{label} ({ds[var].attrs.get('units', '')})", cmap=cmap, **kw)


def c1():
    woa("C1_temperature", "woa23/temperature/1.00/*_t00_01.nc", "t_an", 0,
        "WOA23 temperature, annual 1991-2020, surface", "t_an", "RdYlBu_r", vmin=-2, vmax=31)


def c2():
    woa("C2_salinity", "woa23/salinity/1.00/*_s00_01.nc", "s_an", 0,
        "WOA23 salinity, annual 1991-2020, surface", "s_an", "viridis", vmin=30, vmax=38)


def s4():
    woa("S4_oxygen", "woa23/oxygen/1.00/*_o00_01.nc", "o_an", 500,
        "WOA23 dissolved oxygen, annual, 500 m", "o_an", "magma")


def c3():
    f = first("gebco_2026/GEBCO_2026*.nc")
    if not f:
        return print("skip C3: no file")
    with xr.open_dataset(f) as ds:
        da = ds["elevation"][::60, ::60]  # 15 arc-sec -> 15 arc-min
        single("C3_gebco_elevation", da, "GEBCO_2026 elevation (every 60th cell)", f, "elevation (m)",
               cmap="gist_earth", norm=mcolors.TwoSlopeNorm(vmin=-9000, vcenter=0, vmax=6000))


def s1():
    files = sorted(RAW.glob("oisst_v2.1/sst.day.mean.[0-9]*.nc"))
    if not files:
        return print("skip S1: no file")
    f = files[-2] if len(files) > 1 else files[-1]  # last complete year
    with xr.open_dataset(f) as ds:
        da = ds["sst"].isel(time=0)
        day = str(da.time.values)[:10]
        single("S1_oisst_day", da, f"OISST v2.1 SST, {day}", f, "sst (degC)", cmap="RdYlBu_r",
               vmin=-2, vmax=32)


def s2s3():
    f = first("glorys12/glorys12_monthly_*.nc")
    if not f:
        return print("skip S2+S3: no file")
    with xr.open_dataset(f) as ds:
        top = ds.isel(time=0, depth=0)
        month = str(top.time.values)[:7]
        sub = dict(latitude=slice(None, None, 4), longitude=slice(None, None, 4))
        fig, axes = plt.subplots(2, 1, figsize=(10, 9))
        im = panel(axes[0], top["thetao"].isel(**sub), f"GLORYS12 temperature, {month}, {float(top.depth):.1f} m",
                   f, cmap="RdYlBu_r", vmin=-2, vmax=32)
        fig.colorbar(im, ax=axes[0], label="thetao (degC)", shrink=0.8)
        speed = np.hypot(top["uo"], top["vo"]).isel(**sub)
        im = panel(axes[1], speed, "Current speed", f, cmap="viridis", vmin=0, vmax=1)
        fig.colorbar(im, ax=axes[1], label="speed (m/s)", shrink=0.8)
        save(fig, "S2S3_glorys_surface")


def s5():
    f = first("oceancolor_chl/*.L3m.CU.CHL.chlor_a.4km.nc")
    if not f:
        return print("skip S5: no file")
    with xr.open_dataset(f) as ds:
        da = ds["chlor_a"][::4, ::4]
        single("S5_chlorophyll", da, "MODIS-Aqua chlorophyll-a, mission climatology", f,
               "chlor_a (mg m^-3, log)", cmap="viridis", norm=mcolors.LogNorm(0.01, 20))


def o1():
    f = first("etopo_2022/*.nc")
    if not f:
        return print("skip O1: no file")
    with xr.open_dataset(f) as ds:
        da = ds["z"][::4, ::4]
        single("O1_etopo", da, "ETOPO 2022 60 arc-sec surface elevation", f, "z (m)",
               cmap="gist_earth", norm=mcolors.TwoSlopeNorm(vmin=-9000, vcenter=0, vmax=6000))


PLOTS = {"C1": c1, "C2": c2, "C3": c3, "S1": s1, "S2": s2s3, "S4": s4, "S5": s5, "O1": o1}

if __name__ == "__main__":
    for key in sys.argv[1:] or PLOTS:
        PLOTS[key]()
