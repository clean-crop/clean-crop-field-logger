"""
Precompute the regional soil-moisture trend the logger displays.

Run locally; commit the resulting parquet. The deployed app only ever reads that
file, so it needs neither xarray, nor CDS credentials, nor the raw NetCDF — the
same reason the sibling monitor commits its caches.

    python3 build_soil_moisture.py

Why regional and not per field: ERA5 is a 0.25 deg grid, about 28 km. Every field
in this program falls in one of a couple of cells, so a per-field series would be
the same line drawn several times. This extracts one series for the growing area
and compares it against the 2015-2024 normal for the same calendar days.

Coverage follows the *growing season*, June through October, not the insurance
risk period. The sibling monitor stops at 31 August because that is when its
contracts end; harvest is well after that, so this fetches whatever the monitor
has not already downloaded and caches it here. Re-run it as the season goes on —
ERA5 publishes 5-7 days behind, so there is always a short tail missing.

Reads any raw month the monitor already has before asking CDS for it, so a
re-run costs nothing when the monitor is up to date.
"""

import datetime as dt
from pathlib import Path

import pandas as pd
import xarray as xr

HERE = Path(__file__).parent
MONITOR = HERE.parent / "clean_crop_monitor" / "data_cache"
MONITOR_SEASON_RAW = MONITOR / "raw"
MONITOR_BASELINE_RAW = MONITOR / "smi" / "raw"

LOCAL_RAW = HERE / "era5_cache"            # gitignored; only the parquet is committed
OUT = HERE / "soil_moisture.parquet"

SEASON_YEAR = 2026
SEASON_FIRST_MONTH, SEASON_LAST_MONTH = 6, 10   # June through harvest
BASELINE_YEARS = range(2015, 2025)

# Centre of the growing area. Any field in the program lands in this cell or a
# neighbouring one; ERA5 cannot resolve finer than that anyway.
AREA_LAT, AREA_LON = 36.0, -98.0

# Matches the monitor: plain ERA5 layer 2 (7-28 cm), not ERA5-Land, which was
# tested there and reads 10-20% lower without resolving any finer.
CDS_DATASET = "reanalysis-era5-single-levels"
CDS_VARIABLE = "volumetric_soil_water_layer_2"
BBOX = [37.5, -99.5, 35.0, -96.5]          # N, W, S, E — a buffer around the fields

PUBLICATION_LAG_DAYS = 6


def _month_days(year, month):
    nxt = dt.date(year + (month == 12), (month % 12) + 1, 1)
    return (nxt - dt.timedelta(days=1)).day


def _find_existing(year, month):
    """A raw file for this month, from either cache, or None."""
    names = [f"era5_swvl2_{year}{month:02d}.nc"]
    for d in (MONITOR_SEASON_RAW, LOCAL_RAW):
        for n in names:
            p = d / n
            if p.exists():
                return p
    return None


def _days_covered(path):
    """
    How many distinct days a raw file actually contains. The monitor fetches from
    its risk-period start, so its June file holds 30 June alone — reusing it as if
    it were a whole month silently truncates the season.
    """
    try:
        ds = xr.open_dataset(path)
        tdim = "valid_time" if "valid_time" in ds.dims else "time"
        n = len(pd.to_datetime(ds[tdim].values).normalize().unique())
        ds.close()
        return n
    except Exception:
        return 0


def _download(year, month, days, out_path):
    import cdsapi
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cdsapi.Client().retrieve(
        CDS_DATASET,
        {
            "product_type": "reanalysis",
            "variable": [CDS_VARIABLE],
            "year": [str(year)],
            "month": [f"{month:02d}"],
            "day": [f"{d:02d}" for d in days],
            "time": [f"{h:02d}:00" for h in range(24)],
            "area": BBOX,
            "data_format": "netcdf",
            "download_format": "unarchived",
        },
        str(out_path),
    )


def _series_at(path, lat, lon):
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


def collect_season():
    """Raw files covering the season so far, fetching only what is missing."""
    horizon = dt.date.today() - dt.timedelta(days=PUBLICATION_LAG_DAYS)
    files = []
    for month in range(SEASON_FIRST_MONTH, SEASON_LAST_MONTH + 1):
        if dt.date(SEASON_YEAR, month, 1) > horizon:
            break                                    # month hasn't happened yet
        last = min(_month_days(SEASON_YEAR, month),
                   horizon.day if (month, SEASON_YEAR) == (horizon.month, horizon.year)
                   else _month_days(SEASON_YEAR, month))
        existing = _find_existing(SEASON_YEAR, month)
        is_current = (month == horizon.month)
        if existing:
            have = _days_covered(existing)
            if have >= last and not is_current:
                print(f"  {SEASON_YEAR}-{month:02d}  cached, {have}/{last} days")
                files.append(existing)
                continue
            reason = ("current month, publishes incrementally" if is_current
                      else f"only {have}/{last} days — the monitor fetched a partial month")
            print(f"  {SEASON_YEAR}-{month:02d}  refetching ({reason})")
        target = LOCAL_RAW / f"era5_swvl2_{SEASON_YEAR}{month:02d}.nc"
        print(f"  {SEASON_YEAR}-{month:02d}  requesting days 1-{last} from CDS...")
        try:
            _download(SEASON_YEAR, month, list(range(1, last + 1)), target)
            files.append(target)
            print(f"  {SEASON_YEAR}-{month:02d}  got {_days_covered(target)} days")
        except Exception as e:
            print(f"  {SEASON_YEAR}-{month:02d}  fetch failed ({str(e)[:90]})")
            if existing:
                print(f"  {SEASON_YEAR}-{month:02d}  falling back to the cached partial month")
                files.append(existing)
    return files


def collect_baseline(needed_months):
    """
    Baseline files covering the calendar months the season actually spans. The
    monitor's baseline covers Jun-Aug only, so anything later is fetched here —
    but only once the season reaches it, so a mid-season run costs nothing.
    """
    files = []
    for year in BASELINE_YEARS:
        p = MONITOR_BASELINE_RAW / f"era5_swvl2_baseline_{year}.nc"
        if p.exists():
            files.append(p)
    extra = sorted(m for m in needed_months if m > 8)
    for month in extra:
        for year in BASELINE_YEARS:
            existing = LOCAL_RAW / f"era5_swvl2_baseline_{year}{month:02d}.nc"
            if existing.exists():
                files.append(existing)
                continue
            print(f"  baseline {year}-{month:02d}  fetching from CDS...")
            try:
                _download(year, month, list(range(1, _month_days(year, month) + 1)), existing)
                files.append(existing)
            except Exception as e:
                print(f"  baseline {year}-{month:02d}  failed ({str(e)[:80]})")
    return files


def main():
    print("Season data:")
    season_files = collect_season()
    if not season_files:
        raise SystemExit("No season data available. Is the monitor's cache present?")

    grid = None
    frames = []
    for f in season_files:
        df, grid = _series_at(f, AREA_LAT, AREA_LON)
        frames.append(df)
    current = (pd.concat(frames, ignore_index=True)
               .drop_duplicates(subset="date", keep="last")
               .sort_values("date"))
    current = current[current.date.dt.year == SEASON_YEAR]
    needed_months = sorted(current.date.dt.month.unique())

    print("Baseline:")
    base_files = collect_baseline(needed_months)
    if not base_files:
        raise SystemExit("No baseline data found.")
    bframes = []
    for f in base_files:
        df, _ = _series_at(f, AREA_LAT, AREA_LON)
        bframes.append(df)
    baseline = pd.concat(bframes, ignore_index=True).drop_duplicates(subset="date")
    baseline["md"] = baseline.date.dt.strftime("%m-%d")

    normal = (baseline.groupby("md")["vswc"]
              .agg(normal_low=lambda s: s.quantile(0.10),
                   normal_med="median",
                   normal_high=lambda s: s.quantile(0.90))
              .reset_index())

    current = current.copy()
    current["md"] = current.date.dt.strftime("%m-%d")
    out = current.merge(normal, on="md", how="left").drop(columns="md")
    out["grid_lat"], out["grid_lon"] = grid

    missing = int(out.normal_med.isna().sum())
    out.to_parquet(OUT, index=False)

    print(f"\nWrote {OUT.name}: {len(out)} days "
          f"({out.date.min():%Y-%m-%d} to {out.date.max():%Y-%m-%d})")
    print(f"  grid cell: {grid[0]:.2f}, {grid[1]:.2f}")
    print(f"  baseline: {len(base_files)} file(s), {len(normal)} calendar days")
    if missing:
        print(f"  WARNING: {missing} day(s) have no normal to compare against — "
              f"the baseline does not cover those calendar days yet.")
    print(f"  season mean VSWC {out.vswc.mean()*100:.1f}% "
          f"vs normal median {out.normal_med.mean()*100:.1f}%")


if __name__ == "__main__":
    main()
