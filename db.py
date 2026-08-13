"""
Storage layer for the field logger.

Uses Supabase when credentials are present in Streamlit secrets; otherwise
falls back to local CSV files under local_data/. The fallback exists so the
app is fully usable and testable before Supabase is wired up — but it is
single-machine only, so the deployed app must have Supabase configured or
each user's submissions would land in their own isolated container.
"""

from pathlib import Path

import pandas as pd
import streamlit as st

LOCAL_DIR = Path(__file__).parent / "local_data"
LOCAL_DIR.mkdir(exist_ok=True)

TABLES = ("fields", "field_seasons", "visits")

FIELD_COLS = ["field_id", "grower_name", "farm_name",
              "lat", "lon", "irrigated", "notes", "recorded_by"]
SEASON_COLS = ["field_id", "season_year",
               "planting_date", "acres", "seed_lbs_per_acre",
               "soil_condition_planting", "planting_method", "row_spacing_in",
               "planting_notes", "planting_by",
               "harvest_date", "yield_lbs_per_acre", "harvest_notes", "harvest_by"]
VISIT_COLS = ["field_id", "season_year", "visit_date", "growth_stage",
              "condition_score", "notes", "recorded_by"]

_COLS = {"fields": FIELD_COLS, "field_seasons": SEASON_COLS, "visits": VISIT_COLS}


@st.cache_resource(show_spinner=False)
def _client():
    """Supabase client, or None if not configured (-> CSV fallback)."""
    try:
        url = st.secrets["supabase_url"]
        key = st.secrets["supabase_key"]
    except (KeyError, FileNotFoundError):
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception as e:  # bad credentials, unreachable, lib missing
        st.warning(f"Supabase unavailable ({e}); using local storage.")
        return None


def using_supabase() -> bool:
    return _client() is not None


def _csv_path(table: str) -> Path:
    return LOCAL_DIR / f"{table}.csv"


def _write_csv(table: str, df: pd.DataFrame) -> None:
    """
    Write a table, always carrying the full column set. Without the reindex a
    column nobody has filled in yet is simply absent from the file, so exports
    silently vary in shape depending on what has been entered so far.
    """
    df.reindex(columns=_COLS[table]).to_csv(_csv_path(table), index=False)


def read(table: str) -> pd.DataFrame:
    """Read a whole table. Always returns a DataFrame with the expected columns."""
    assert table in TABLES, table
    cli = _client()
    if cli is not None:
        try:
            rows = cli.table(table).select("*").execute().data
            df = pd.DataFrame(rows)
            return df if len(df) else pd.DataFrame(columns=_COLS[table])
        except Exception as e:
            st.error(f"Read from Supabase failed: {e}")
            return pd.DataFrame(columns=_COLS[table])
    p = _csv_path(table)
    if p.exists():
        return pd.read_csv(p)
    return pd.DataFrame(columns=_COLS[table])


def insert(table: str, record: dict):
    """Insert one row. Returns (ok, message)."""
    assert table in TABLES, table
    record = {k: v for k, v in record.items() if k in _COLS[table]}
    cli = _client()
    if cli is not None:
        try:
            cli.table(table).insert(record).execute()
            return True, "Saved."
        except Exception as e:
            return False, f"Save failed: {e}"
    df = read(table)
    new = pd.DataFrame([record])
    df = pd.concat([df, new], ignore_index=True) if len(df) else new
    _write_csv(table, df)
    return True, "Saved locally."


def upsert_season(record: dict):
    """
    Insert or update the row for (field_id, season_year) — used so harvest data
    can be added later to a planting record without creating a duplicate.
    """
    # Drop blanks so the planting save and the harvest save, which each send only
    # their own half of the row, never write emptiness over the other's answers.
    keys = ("field_id", "season_year")
    record = {k: v for k, v in record.items()
              if k in SEASON_COLS and (k in keys or (v is not None and v != ""))}
    cli = _client()
    if cli is not None:
        try:
            cli.table("field_seasons").upsert(
                record, on_conflict="field_id,season_year"
            ).execute()
            return True, "Saved."
        except Exception as e:
            return False, f"Save failed: {e}"

    df = read("field_seasons")
    hit = None
    if len(df):
        mask = ((df.field_id == record["field_id"]) &
                (df.season_year == record["season_year"]))
        if mask.any():
            hit = df.index[mask][0]

    if hit is not None:
        # Rebuild the row as a dict rather than assigning into the DataFrame —
        # writing e.g. a date string into a column pandas inferred as float
        # raises in newer pandas.
        row = df.loc[hit].to_dict()
        row.update({k: v for k, v in record.items() if v is not None and v != ""})
        rest = df.drop(index=hit)
        df = pd.concat([rest, pd.DataFrame([row])], ignore_index=True) if len(rest) \
            else pd.DataFrame([row])
    else:
        new = pd.DataFrame([record])
        df = pd.concat([df, new], ignore_index=True) if len(df) else new

    _write_csv("field_seasons", df)
    return True, "Saved locally."


def update_field(field_id: str, changes: dict):
    """
    Edit a registered field in place. `changes` may include a new `field_id`;
    the rename is propagated to field_seasons and visits so the season-over-season
    link survives — that link is the entire point of the tool.
    """
    changes = {k: v for k, v in changes.items() if k in FIELD_COLS}
    new_id = changes.get("field_id", field_id)

    cli = _client()
    if cli is not None:
        try:
            # The FKs are declared `on update cascade`, so Postgres carries the
            # rename into the child tables by itself.
            cli.table("fields").update(changes).eq("field_id", field_id).execute()
            return True, "Saved."
        except Exception as e:
            return False, f"Update failed: {e}"

    fields = read("fields")
    if not len(fields) or field_id not in set(fields.field_id.astype(str)):
        return False, f"No field '{field_id}'."
    # Column by column, widening dtype first: a column nobody has filled in reads
    # back as all-NaN float64, and writing a string into it raises in new pandas.
    mask = fields.field_id == field_id
    for col, val in changes.items():
        if col not in fields.columns:
            fields[col] = None
        if isinstance(val, str) and fields[col].dtype != object:
            fields[col] = fields[col].astype(object)
        fields.loc[mask, col] = val
    _write_csv("fields", fields)

    if new_id != field_id:                       # hand-rolled cascade for the CSVs
        for t in ("field_seasons", "visits"):
            d = read(t)
            if len(d):
                d.loc[d.field_id == field_id, "field_id"] = new_id
                _write_csv(t, d)
    return True, "Saved locally."


def delete_field(field_id: str):
    """
    Remove a field and everything recorded against it. Destructive and not
    undoable — the caller is responsible for confirming first.
    Returns (ok, message, counts_removed).
    """
    counts = {}
    for t in ("field_seasons", "visits"):
        d = read(t)
        counts[t] = int((d.field_id == field_id).sum()) if len(d) else 0

    cli = _client()
    if cli is not None:
        try:
            # `on delete cascade` clears the child rows with it.
            cli.table("fields").delete().eq("field_id", field_id).execute()
            return True, f"Deleted {field_id}.", counts
        except Exception as e:
            return False, f"Delete failed: {e}", counts

    for t in ("field_seasons", "visits", "fields"):
        d = read(t)
        if len(d):
            _write_csv(t, d[d.field_id != field_id])
    return True, f"Deleted {field_id}.", counts


def suggest_field_id(grower_name: str, existing: pd.DataFrame) -> str:
    """
    Propose a stable, readable field id like 'DOE-02', built from the grower's
    surname. Offered as a placeholder for the grower to type, not pre-filled —
    the ID is the one value that must stay identical year after year, so it is
    worth a deliberate keystroke rather than an accepted default.
    """
    parts = [p for p in (grower_name or "").split() if p]
    stem = "".join(c for c in (parts[-1] if parts else "FIELD").upper()
                   if c.isalnum())[:10] or "FIELD"
    n = 1
    if len(existing) and "field_id" in existing:
        used = set(existing["field_id"].astype(str))
        while f"{stem}-{n:02d}" in used:
            n += 1
    return f"{stem}-{n:02d}"
