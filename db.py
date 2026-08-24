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

TABLES = ("fields", "field_seasons", "visits", "photos")

FIELD_COLS = ["field_id", "grower_name", "farm_name",
              "lat", "lon", "irrigated", "notes", "recorded_by"]
SEASON_COLS = ["field_id", "season_year",
               "planting_date", "acres", "seed_lbs_per_acre",
               "soil_condition_planting", "planting_method", "row_spacing_in",
               "planting_notes", "planting_by",
               "harvest_date", "yield_lbs_per_acre", "harvest_notes", "harvest_by"]
VISIT_COLS = ["id", "field_id", "season_year", "visit_date", "growth_stage",
              "condition_score", "notes", "management_notes", "recorded_by"]
PHOTO_COLS = ["id", "field_id", "season_year", "stage", "visit_id",
              "storage_path", "caption", "taken_by"]

_COLS = {"fields": FIELD_COLS, "field_seasons": SEASON_COLS,
         "visits": VISIT_COLS, "photos": PHOTO_COLS}

PHOTO_BUCKET = "field-photos"
MAX_EDGE = 1600        # px; a phone photo is far larger than anyone needs here
JPEG_QUALITY = 82


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


def _is_missing_table(err) -> bool:
    s = str(err).lower()
    return "schema cache" in s or "does not exist" in s or "42p01" in s


def photos_enabled() -> bool:
    """Has schema_photos.sql been run? Local storage is always ready."""
    cli = _client()
    if cli is None:
        return True
    try:
        cli.table("photos").select("id").limit(1).execute()
        return True
    except Exception as e:
        if _is_missing_table(e):
            return False
        return True


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
            # `photos` is optional — it only exists once schema_photos.sql has been
            # run. Absent is a legitimate not-configured state, not a fault, and
            # shouting a Postgres error on every tab helps nobody. The core tables
            # still report loudly, because their absence really is broken.
            if table == "photos" and _is_missing_table(e):
                return pd.DataFrame(columns=_COLS[table])
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
    # Postgres hands out `id` itself; the CSV fallback has to mint one, so that a
    # single row can still be addressed later for editing.
    if "id" in _COLS[table] and "id" not in record:
        used = pd.to_numeric(df["id"], errors="coerce") if "id" in df else pd.Series(dtype=float)
        record = {"id": int(used.max()) + 1 if used.notna().any() else 1, **record}
    new = pd.DataFrame([record])
    df = pd.concat([df, new], ignore_index=True) if len(df) else new
    _write_csv(table, df)
    return True, "Saved locally."


def insert_returning(table: str, record: dict):
    """
    Insert one row and hand back its id, so callers can attach children to it —
    photos to the visit they were taken on, for instance.
    Returns (ok, message, row_id).
    """
    assert table in TABLES, table
    record = {k: v for k, v in record.items() if k in _COLS[table]}
    cli = _client()
    if cli is not None:
        try:
            res = cli.table(table).insert(record).execute()
            new_id = res.data[0]["id"] if res.data and "id" in res.data[0] else None
            return True, "Saved.", new_id
        except Exception as e:
            return False, f"Save failed: {e}", None

    df = read(table)
    used = pd.to_numeric(df["id"], errors="coerce") if "id" in df else pd.Series(dtype=float)
    new_id = int(used.max()) + 1 if used.notna().any() else 1
    record = {"id": new_id, **record}
    df = pd.concat([df, pd.DataFrame([record])], ignore_index=True) if len(df) \
        else pd.DataFrame([record])
    _write_csv(table, df)
    return True, "Saved locally.", new_id


def update_row(table: str, row_id, changes: dict):
    """Edit one row addressed by its `id`. Returns (ok, message)."""
    assert table in TABLES, table
    changes = {k: v for k, v in changes.items() if k in _COLS[table] and k != "id"}
    cli = _client()
    if cli is not None:
        try:
            cli.table(table).update(changes).eq("id", row_id).execute()
            return True, "Saved."
        except Exception as e:
            return False, f"Update failed: {e}"

    df = read(table)
    if not len(df) or "id" not in df:
        return False, "Nothing to update."
    mask = pd.to_numeric(df["id"], errors="coerce") == float(row_id)
    if not mask.any():
        return False, f"No row with id {row_id}."
    for col, val in changes.items():
        if col not in df.columns:
            df[col] = None
        if isinstance(val, str) and df[col].dtype != object:
            df[col] = df[col].astype(object)
        df.loc[mask, col] = val
    _write_csv(table, df)
    return True, "Saved locally."


def delete_row(table: str, row_id):
    """Remove one row addressed by its `id`. Returns (ok, message)."""
    assert table in TABLES, table
    cli = _client()
    if cli is not None:
        try:
            cli.table(table).delete().eq("id", row_id).execute()
            return True, "Deleted."
        except Exception as e:
            return False, f"Delete failed: {e}"

    df = read(table)
    if not len(df) or "id" not in df:
        return False, "Nothing to delete."
    keep = pd.to_numeric(df["id"], errors="coerce") != float(row_id)
    _write_csv(table, df[keep])
    return True, "Deleted locally."


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
    for t in ("field_seasons", "visits", "photos"):
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

    # Postgres cascades through the FKs; the CSV fallback has to do it by hand,
    # including the image files, or a deleted field leaves its photos orphaned.
    gone = read("photos")
    if len(gone) and "storage_path" in gone:
        for sp in gone.loc[gone.field_id == field_id, "storage_path"]:
            f = LOCAL_DIR / "photos" / str(sp)
            if f.exists():
                f.unlink()
    for t in ("field_seasons", "visits", "photos", "fields"):
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


# ── Photos ─────────────────────────────────────────────────────────────────
# Files live in Supabase Storage; the `photos` table only records what each one
# is a picture of. Locally, files go under local_data/photos/ so the CSV
# fallback stays fully usable.

def _shrink(raw: bytes) -> bytes:
    """
    Downscale to something sensible before upload. A phone photo is 3-12 MB and
    the free Storage tier is 1 GB, so uploading originals would fill it after a
    few hundred pictures. Longest edge capped, re-encoded as JPEG.

    exif_transpose first: phones record orientation as metadata rather than
    rotating the pixels, and re-encoding drops the metadata — so without this,
    photos taken sideways get saved sideways, permanently.
    """
    try:
        import io
        from PIL import Image, ImageOps
    except ImportError:
        return raw                      # Pillow missing: store as-is rather than fail

    try:
        img = Image.open(io.BytesIO(raw))
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.thumbnail((MAX_EDGE, MAX_EDGE))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        return out.getvalue()
    except Exception:
        return raw


def add_photo(raw: bytes, *, field_id, stage, season_year=None,
              visit_id=None, caption="", taken_by=""):
    """Store one image and record what it shows. Returns (ok, message)."""
    import uuid
    data = _shrink(raw)
    path = f"{field_id}/{season_year or 'field'}/{stage}/{uuid.uuid4().hex}.jpg"

    cli = _client()
    if cli is not None:
        try:
            cli.storage.from_(PHOTO_BUCKET).upload(
                path, data, {"content-type": "image/jpeg"})
        except Exception as e:
            msg = str(e)
            if "Bucket not found" in msg or "not found" in msg.lower():
                return False, ("Photo storage isn't set up yet — run "
                               "`schema_photos.sql` in the Supabase SQL editor.")
            return False, f"Upload failed: {msg[:200]}"
    else:
        local = LOCAL_DIR / "photos" / path
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(data)

    ok, msg = insert("photos", dict(
        field_id=field_id, season_year=season_year, stage=stage,
        visit_id=visit_id, storage_path=path, caption=caption, taken_by=taken_by))
    if not ok:
        return False, msg
    return True, f"Photo saved ({len(data)//1024} KB)."


def photos_for(field_id, season_year=None, stage=None, visit_id=None):
    """The photo rows matching this field, narrowed by whatever is given."""
    df = read("photos")
    if not len(df) or "field_id" not in df:
        return df
    m = df.field_id == field_id
    if season_year is not None and "season_year" in df:
        m &= pd.to_numeric(df.season_year, errors="coerce") == float(season_year)
    if stage is not None and "stage" in df:
        m &= df.stage == stage
    if visit_id is not None and "visit_id" in df:
        m &= pd.to_numeric(df.visit_id, errors="coerce") == float(visit_id)
    return df[m]


def photo_bytes(storage_path: str):
    """Raw image bytes for display, or None."""
    cli = _client()
    if cli is not None:
        try:
            return cli.storage.from_(PHOTO_BUCKET).download(storage_path)
        except Exception:
            return None
    p = LOCAL_DIR / "photos" / storage_path
    return p.read_bytes() if p.exists() else None


def delete_photo(row_id, storage_path):
    """Remove the file and its row. Returns (ok, message)."""
    cli = _client()
    if cli is not None:
        try:
            cli.storage.from_(PHOTO_BUCKET).remove([storage_path])
        except Exception:
            pass          # orphaned file is better than a row pointing nowhere
    else:
        p = LOCAL_DIR / "photos" / storage_path
        if p.exists():
            p.unlink()
    return delete_row("photos", row_id)
