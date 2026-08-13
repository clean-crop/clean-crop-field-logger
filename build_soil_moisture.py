"""
Precompute the regional soil-moisture trend the logger displays.

Run locally; commit the resulting parquet. The deployed app only ever reads
that file, so it needs neither xarray, nor CDS credentials, nor the raw
NetCDF — which is the same reason the sibling monitor commits its caches.

    python3 build_soil_moisture.py

Why regional and not per field: ERA5 is a 0.25 deg grid, about 28 km. Every
field in this program falls in one of a couple of cells, so a per-field series
would be the same line drawn several times. This extracts one series for the
growing area and compares it against the 2015-2024 normal for the same cell.

Source data lives in the sibling monitoring project, which owns the CDS pull:
    ../clean_crop_monitor/data_cache/raw/            (current season)
    ../clean_crop_monitor/data_cache/smi/raw/        (2015-2024 baseline)
"""

from pathlib import Path

import pandas as pd
import xarray as xr

HERE = Path(__file__).parent
MONITOR = HERE.parent / "clean_crop_monitor" / "data_cache"
CURRENT_RAW = MONITOR / "raw"
BASELINE_RAW = MONITOR / "smi" / "raw"
OUT = HERE / "soil_moisture.parquet"

SEASON_YEAR = 2026
# Centre of the growing area. Any field in the program lands in this cell or a
# neighbouring one; ERA5 cannot resolve finer than that anyway.
AREA_LAT, AREA_LON = 36.0, -98.0


def _series_at(path: Path, lat: float, lon: float) -> pd.DataFrame:
    """Daily mean VSWC at the grid cell nearest (lat, lon)."""
    ds = xr.open_dataset(path)
    var = "swvl2" if "swvl2" in ds else list(ds.data_vars)[0]
    tdim = "valid_time" if "valid_time" in ds.dims else "time"
    point = ds[var].sel(latitude=lat, longitude=lon, method="nearest")
    grid = (float(point.latitude), float(point.longitude))
    daily = point.resample({tdim: "1D"}).mean()
    df = daily.to_dataframe(name="vswc").reset_index()[[tdim, "vswc"]]
    df.columns = ["date", "vswc"]
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    ds.close()
    return df.dropna(subset=["vswc"]), grid


def main():
    if not CURRENT_RAW.exists():
        raise SystemExit(f"No source data at {CURRENT_RAW}. The monitoring project "
                         f"owns the CDS pull — run its refresh first.")

    grid = None

    # ── current season ─────────────────────────────────────────────────────
    cur = []
    for f in sorted(CURRENT_RAW.glob("era5_swvl2_*.nc")):
        df, grid = _series_at(f, AREA_LAT, AREA_LON)
        cur.append(df)
    current = (pd.concat(cur, ignore_index=True)
               .drop_duplicates(subset="date", keep="last")
               .sort_values("date"))
    current = current[current.date.dt.year == SEASON_YEAR]

    # ── 2015-2024 normal, by calendar day ──────────────────────────────────
    base = []
    for f in sorted(BASELINE_RAW.glob("era5_swvl2_baseline_*.nc")):
        df, _ = _series_at(f, AREA_LAT, AREA_LON)
        base.append(df)
    baseline = pd.concat(base, ignore_index=True)
    baseline["md"] = baseline.date.dt.strftime("%m-%d")

    normal = (baseline.groupby("md")["vswc"]
              .agg(normal_low=lambda s: s.quantile(0.10),
                   normal_med="median",
                   normal_high=lambda s: s.quantile(0.90))
              .reset_index())

    # ── align this season against the normal for the same calendar day ─────
    current["md"] = current.date.dt.strftime("%m-%d")
    out = current.merge(normal, on="md", how="left").drop(columns="md")
    out["grid_lat"], out["grid_lon"] = grid
    out.to_parquet(OUT, index=False)

    span = f"{out.date.min():%Y-%m-%d} to {out.date.max():%Y-%m-%d}"
    print(f"Wrote {OUT.name}: {len(out)} days ({span})")
    print(f"  grid cell: {grid[0]:.2f}, {grid[1]:.2f}")
    print(f"  baseline: {len(base)} years, {len(normal)} calendar days")
    print(f"  season mean VSWC {out.vswc.mean()*100:.1f}% "
          f"vs normal median {out.normal_med.mean()*100:.1f}%")


if __name__ == "__main__":
    main()
