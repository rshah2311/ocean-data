# ocean-data

Global ocean datasets (temperature, salinity, bathymetry, SST, currents, oxygen,
chlorophyll), the scripts that fetch and check them, and a catalogue of every file.
This repo **gathers** data only: no analysis or derived fields.

## Status

| Phase | Dataset | Status |
|---|---|---|
| 0 | Dry run (`config/sources.yaml`) | done |
| 1 | C1/C2 WOA23 temperature + salinity, 1° | done, 34 files ok |
| 2 | C3/C4 GEBCO_2026 grid + TID | done, all ok |
| 3 | C1/C2 WOA23 temperature + salinity, 0.25° (~22 GB) | skipped for now |
| 4 | S4 WOA23 oxygen, 1° | done, 17 files ok |
| 5 | S2/S3 GLORYS12 monthly, last 24 months (2024-09 → 2026-08) | done, 3 files ok (5.3 GB) |
| 6 | S1 OISST daily, 2024 → 2026-10-24 + land mask | done, 4 files ok |
| 7 | S5 MODIS-Aqua chlorophyll climatologies | done, 13 files ok |
| 8 | O1 ETOPO 2022 (optional), 60 arc-sec surface | done, 1 file ok |

## Layout

```
config/sources.yaml     every file to fetch: URL, local path, size (written by list_sources.py)
data/raw/               downloaded files, never edited (git-ignored)
  woa23/{temperature,salinity,oxygen}/{1.00,0.25}/
  gebco_2026/           zips + unzipped netCDF side by side
  oisst_v2.1/
  glorys12/
  oceancolor_chl/
  etopo_2022/
catalog/manifest.csv    one row per file: size, SHA-256, variables, dims, ranges, status
catalog/quicklooks/     one sanity-check map per dataset
scripts/                list_sources.py, download.py, download_glorys.py, verify.py, quicklook.py, manifest.py
viz/web/index.html      interactive map page (published as an artifact)
viz/web/data/           map images built by scripts/build_viz.py (git-ignored)
```

## Setup

Python 3.12, Windows. Create the environment and install the pinned packages:

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
```

Some packages are pinned to slightly older releases (pandas 2.2.3, netCDF4 1.7.2,
h5py 3.12.1, numcodecs 0.16.1, pyarrow 19.0.1). On this machine Windows Application
Control blocks the DLLs in the newest releases; the older ones load fine.

## Re-running

```bash
.venv/Scripts/python -I scripts/list_sources.py          # re-resolve URLs and sizes (or: list_sources.py S1)
.venv/Scripts/python -I scripts/download.py C1 C2        # fetch datasets by id
.venv/Scripts/python -I scripts/verify.py C1 C2          # open files, run checks, update manifest
.venv/Scripts/python -I scripts/quicklook.py             # redraw quicklook maps
.venv/Scripts/python -I scripts/build_viz.py             # rebuild the web map images (or: build_viz.py glorys)
```

`download.py` resumes partial files (`*.part`), retries with backoff, skips files
already on disk at the right size, records SHA-256, and unzips zips next to the zip.
It runs at most 4 downloads per host at once. Dataset ids: `C1 C2` (1°), `C1q C2q`
(0.25°), `C3+C4`, `S4`, `S1`, `S5`, `O1`. GLORYS (`S2+S3`) has its own script:

```bash
.venv/Scripts/copernicusmarine login                  # once, stores credentials in ~/.copernicusmarine
.venv/Scripts/python -I scripts/download_glorys.py    # or: download_glorys.py 2025
```

## Datasets

### C1, C2: WOA23 temperature and salinity (NOAA NCEI)
- Source: `https://www.ncei.noaa.gov/data/oceans/woa/WOA23/DATA/{temperature,salinity}/netcdf/decav91C0/{1.00,0.25}/`
- Files: `woa23_decav91C0_<v><tt>_<gg>.nc`. `<v>` = `t` or `s`. `<tt>` = `00` annual,
  `01`–`12` months, `13`–`16` seasons (Jan–Mar, Apr–Jun, Jul–Sep, Oct–Dec). `<gg>` = `01` (1°) or `04` (0.25°).
- 1991–2020 climate normal. Monthly files have 57 depth levels (0–1500 m); annual and seasonal have 102 (0–5500 m).
- Open with `xr.open_dataset(f, decode_times=False)`. Time units are "months since 1991-01-01" (climatology).
- Variables: `_an` objectively analysed mean, `_mn` statistical mean, `_dd` number of
  observations, `_sd` standard deviation, `_se` standard error, `_oa` analysed minus mean,
  `_gp` grid points used, `_sdo`, `_sea` (analysed standard deviation / standard error).
  `_ma` (period minus annual mean) is in monthly and seasonal files only, not in the annual `00` file.
- Longitude −180 to 180.

### S4: WOA23 dissolved oxygen (NOAA NCEI)
- Source: `https://www.ncei.noaa.gov/data/oceans/woa/WOA23/DATA/oxygen/netcdf/all/1.00/`
- Files: `woa23_all_o<tt>_01.nc`, same `<tt>` codes as above. Period `all` = all years of data
  (the server also has `decav71A0`, 1971–2000, not pulled).
- Annual file has 102 depth levels (0–5500 m). **Monthly and seasonal files have 57 (0–1500 m)**,
  unlike temperature/salinity, whose seasonal files go to 5500 m.
- `o_an` in micromoles per kilogram. Variables as for C1/C2; `o_sdo` is only in the annual file.

### S2, S3: GLORYS12V1 monthly means (Copernicus Marine)
- Product `GLOBAL_MULTIYEAR_PHY_001_030`, dataset `cmems_mod_glo_phy_my_0.083deg_P1M-m`
  (1/12°, 2041 × 4320, lat −80 to 90, lon −180 to 180; available 1993-01 → 2026-08).
- Variables `thetao` (°C), `so` (1e-3), `uo`, `vo` (m/s).
- **Time window: last 24 months, 2024-09 → 2026-08** (cut from the plan's 10 years, chosen in Phase 5).
- Depths: the levels nearest 0, 10, 25, 50, 100, 200, 300, 500, 1000 m, i.e.
  0.494, 9.573, 25.21, 47.37, 92.33, 186.1, 318.1, 541.1, 1062.4 m.
- Files: `glorys12_monthly_<year>.nc`, one per calendar year (2024 = Sep–Dec, 2026 = Jan–Aug).
- How: the toolbox's `subset` only takes a depth *range*, so `download_glorys.py` uses the toolbox's
  `open_dataset` on the same server data, selects the 9 levels, and writes the year with the
  server's int16 packing plus zlib compression. Values are not changed. Each month is ~230 MB.
- Cost note: the server's chunks span many depths, so network transfer is ~11× the data kept
  (toolbox dry-run estimate). Ten years would have been ~30 GB on disk but ~900 GB transferred.

### S1: OISST v2.1 daily sea surface temperature (NOAA PSL)
- Source: `https://downloads.psl.noaa.gov/Datasets/noaa.oisst.v2.highres/`
- Files: `sst.day.mean.<year>.nc` for **2024, 2025, 2026** (cut from the plan's 1991 start, chosen in
  Phase 6), plus `lsmask.oisst.nc` (land–sea mask).
- 0.25° grid, 720 × 1440, daily. `sst` in °C, surface only. Blends satellite, ship, buoy and Argo data with no gaps.
- 2024 = 366 days, 2025 = 365. **2026 is partial** (297 days, to 2026-10-24 when downloaded); NOAA updates it daily,
  so re-running `download.py S1` after `list_sources.py S1` refreshes it (its size changes).
- **Longitude is 0–360**, not −180–180. Kept as is.

### S5: MODIS-Aqua chlorophyll-a climatologies (NASA OB.DAAC)
- Collection `MODISA_L3m_CHL` version 2022.0 (processing R2022.0.3), found via NASA CMR; files from
  `https://obdaac-tea.earthdatacloud.nasa.gov/ob-cumulus-prod-public/`. **Needs a NASA Earthdata login**
  in `~/.netrc` (`machine urs.earthdata.nasa.gov login … password …`), never in the repo.
- Files: `AQUA_MODIS.<start>_<end>.L3m.<MC|CU>.CHL.chlor_a.4km.nc`.
  - `MC` = monthly climatology: 12 files, one per calendar month, averaged over all mission years.
    NASA re-issues each one yearly; the newest was picked, so end years differ (2025 or 2026).
  - `CU` = whole-mission composite (2002-07-04 → 2025-11-30). Stands in for the plan's "annual
    climatology", which OB.DAAC does not publish separately.
- 4 km grid, 4320 × 8640, lat −90 to 90, lon −180 to 180. `chlor_a` in mg m⁻³ (OCI algorithm);
  a `palette` variable is NASA's display colour table. Best viewed on a log scale.

### O1: ETOPO 2022, 60 arc-second surface elevation (NOAA NCEI) — optional
- Source: `https://www.ngdc.noaa.gov/thredds/fileServer/global/ETOPO2022/60s/60s_surface_elev_netcdf/ETOPO_2022_v1_60s_N90W180_surface.nc`
- One file, 478 MB. 10800 × 21600 grid, lat −90 to 90, lon −180 to 180. `z` in metres (negative below sea level).
- A lighter alternative to GEBCO (about 4× coarser). Because cells average over a bigger area, extremes are
  smoothed: Challenger Deep reads −10,540 m (GEBCO −10,931 m), the highest cell near Everest 8,157 m.

### C3, C4: GEBCO_2026 grid and TID grid
- Source: GEBCO, hosted at CEDA:
  `https://dap.ceda.ac.uk/bodc/gebco/global/gebco_2026/ice_surface_elevation/netcdf/GEBCO_2026.zip` and
  `.../type_identifier_grid/netcdf/gebco_2026_tid.zip`. The older BODC download links no longer work.
- 15 arc-second grid, 43200 × 86400. `elevation` in metres (negative below sea level).
  `tid` codes the source type for each cell. Ice-surface version; GEBCO also publishes a sub-ice version (not pulled).
- Unzipped: `GEBCO_2026.nc` (7.5 GB), `GEBCO_2026_TID.nc` (3.7 GB), plus GEBCO's documentation and terms-of-use PDFs. Zips are kept.
- Lat −90 to 90, lon −180 to 180, pixel-centre registered.

## Sanity checks (flagged, not fixed)

| Check | Result |
|---|---|
| WOA23 tropical (10°S–10°N) mean surface temperature, 26–30 °C | 27.8 °C ✓ |
| WOA23 tropical mean temperature at 1000 m, ~4–7 °C | 5.0 °C ✓ |
| GEBCO Challenger Deep ≈ −10,900 m | −10,931 m ✓ |
| GEBCO negative over ocean, positive over land | mid-Pacific −4,332 m, central Australia +349 m ✓ |
| GEBCO and TID grids the same size | both 43200 × 86400 ✓ |
| GLORYS tropical mean temperature, first month of each file | 27.7 / 28.0 / 27.8 °C at 0.5 m; 4.7–4.8 °C at 1062 m ✓ |
| GLORYS months per file | 2024 = 4, 2025 = 12, 2026 = 8 ✓ |
| ETOPO negative over ocean, positive over land | mid-Pacific −4,330 m, central Australia +347 m ✓ |
| OISST days per year (365/366) | 2024 = 366, 2025 = 365 ✓; 2026 partial |
| No dataset all-NaN | checked per file in `verify.py` ✓ |

## Citations
- Locarnini, R. A., et al. (2024). *World Ocean Atlas 2023, Volume 1: Temperature*. NOAA Atlas NESDIS 89.
- Reagan, J. R., et al. (2024). *World Ocean Atlas 2023, Volume 2: Salinity*. NOAA Atlas NESDIS 90.
- Garcia, H. E., et al. (2024). *World Ocean Atlas 2023, Volume 3: Dissolved Oxygen, Apparent Oxygen Utilization, Dissolved Oxygen Saturation and 30-year Climate Normal*. NOAA Atlas NESDIS 91.
- GEBCO Compilation Group (2026). *GEBCO_2026 Grid*. doi:10.5285/4f68d5c7-45eb-f999-e063-7086abc036fa
- Huang, B., et al. (2021). Improvements of the Daily Optimum Interpolation Sea Surface Temperature (DOISST) Version 2.1. *J. Climate* 34, 2923–2939. Data: NOAA PSL.
- E.U. Copernicus Marine Service. *Global Ocean Physics Reanalysis* (GLOBAL_MULTIYEAR_PHY_001_030, GLORYS12V1). doi:10.48670/moi-00021
- NASA Ocean Biology Processing Group. *MODIS-Aqua Level-3 Mapped Chlorophyll Data, version 2022.0*. NASA OB.DAAC. doi:10.5067/AQUA/MODIS/L3M/CHL/2022
- NOAA National Centers for Environmental Information (2022). *ETOPO 2022 15 Arc-Second Global Relief Model*. doi:10.25921/fd45-gt74
