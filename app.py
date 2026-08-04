"""
Clean Crop — Mung Bean Field Logger

Phone-friendly data collection for dryland mung bean fields. Separate from the
Arbol soil-moisture monitoring dashboard; this one is about building a season-
over-season panel: stable field IDs + GPS + planting/management + harvest.

Gated by a shared passcode (set `app_passcode` in Streamlit secrets).
"""

import datetime as dt

import pandas as pd
import streamlit as st

import db

st.set_page_config(page_title="Field Logger — Mung Beans",
                   page_icon="🌱", layout="centered")   # centered = better on phones


# ── Passcode gate ──────────────────────────────────────────────────────────
def _gate():
    """Block all content until the shared passcode is entered."""
    try:
        expected = st.secrets["app_passcode"]
    except (KeyError, FileNotFoundError):
        return True  # no passcode configured (local dev) — allow through

    if st.session_state.get("_authed"):
        return True

    st.title("🌱 Field Logger")
    st.caption("Enter the passcode to continue.")
    pin = st.text_input("Passcode", type="password")
    if st.button("Enter", type="primary"):
        if pin == expected:
            st.session_state["_authed"] = True
            st.rerun()
        else:
            st.error("Incorrect passcode.")
    return False


if not _gate():
    st.stop()

st.title("🌱 Mung Bean Field Logger")
if not db.using_supabase():
    st.warning(
        "**Local storage mode** — saving to CSV on this machine only. "
        "Configure `supabase_url` / `supabase_key` in secrets before field use, "
        "or submissions from different phones won't be shared.",
        icon="⚠️",
    )

fields_df = db.read("fields")
YEAR_DEFAULT = dt.date.today().year

tab_new, tab_plant, tab_visit, tab_harvest, tab_data = st.tabs(
    ["📍 New Field", "🌱 Planting", "🔍 Visit", "🚜 Harvest", "📋 Data"]
)

# ── New Field: register a physical field once, reuse forever ───────────────
with tab_new:
    st.subheader("Register a new field")
    st.caption(
        "Do this once per physical field. The **Field ID** is what links this "
        "field across seasons — reuse it every year rather than creating a new one."
    )

    c1, c2 = st.columns(2)
    grower = c1.text_input("Grower name *", key="nf_grower")
    farm = c2.text_input("Farm name", key="nf_farm")
    c3, c4 = st.columns(2)
    town = c3.text_input("Nearest town *", key="nf_town", placeholder="Loyal")
    county = c4.text_input("County", key="nf_county", placeholder="Kingfisher")

    st.markdown("**Location** — stand in the field and pin it, or type coordinates.")
    m1, m2 = st.columns(2)
    lat = m1.number_input("Latitude *", value=36.0, format="%.6f", key="nf_lat")
    lon = m2.number_input("Longitude *", value=-98.0, format="%.6f", key="nf_lon")

    try:
        import folium
        from streamlit_folium import st_folium

        fmap = folium.Map(location=[lat, lon], zoom_start=14,
                          tiles="Esri.WorldImagery", attr="Esri")
        folium.Marker([lat, lon], tooltip="Current pin").add_to(fmap)
        st.caption("Tap the map to move the pin, then press **Use pinned location**.")
        clicked = st_folium(fmap, height=320, width=None,
                            returned_objects=["last_clicked"], key="nf_map")
        if clicked and clicked.get("last_clicked"):
            cl = clicked["last_clicked"]
            st.info(f"Pinned: {cl['lat']:.6f}, {cl['lng']:.6f}")
            if st.button("Use pinned location", key="nf_usepin"):
                st.session_state.nf_lat = round(cl["lat"], 6)
                st.session_state.nf_lon = round(cl["lng"], 6)
                st.rerun()
    except Exception:
        st.map(pd.DataFrame({"lat": [lat], "lon": [lon]}), zoom=12)

    irrigated = st.checkbox("Irrigated (leave unchecked for dryland)", key="nf_irr")
    fid_default = db.suggest_field_id(town, fields_df)
    fid = st.text_input("Field ID *", value=fid_default, key="nf_fid",
                        help="Stable identifier reused every season, e.g. LOYAL-01")
    nf_notes = st.text_area("Notes", key="nf_notes",
                            placeholder="Anything notable about this ground — "
                                        "creek bottom, drainage, past problems…")

    if st.button("Save field", type="primary", key="nf_save"):
        if not (grower and town and fid):
            st.error("Grower, nearest town, and Field ID are required.")
        elif len(fields_df) and fid in set(fields_df.field_id.astype(str)):
            st.error(f"Field ID '{fid}' already exists — pick a different one.")
        else:
            ok, msg = db.insert("fields", dict(
                field_id=fid, grower_name=grower, farm_name=farm,
                location_name=town, county=county, lat=lat, lon=lon,
                irrigated=irrigated, notes=nf_notes))
            (st.success if ok else st.error)(msg)
            if ok:
                st.cache_data.clear()


# ── shared helper ──────────────────────────────────────────────────────────
def pick_field(key, label="Field *"):
    """Field selector shared by the planting/visit/harvest tabs."""
    if not len(fields_df):
        st.info("No fields registered yet — add one on the **New Field** tab first.")
        return None
    opts = {f"{r.field_id} — {r.grower_name} ({r.location_name})": r.field_id
            for r in fields_df.itertuples()}
    return opts[st.selectbox(label, list(opts), key=key)]


# ── Planting ───────────────────────────────────────────────────────────────
with tab_plant:
    st.subheader("Planting record")
    fid = pick_field("pl_fid")
    if fid:
        year = st.number_input("Season year *", 2020, 2100, YEAR_DEFAULT, key="pl_yr")
        pdate = st.date_input("Planting date *", value=dt.date.today(), key="pl_date")
        c1, c2 = st.columns(2)
        acres = c1.number_input("Acres planted *", 0.0, step=10.0, key="pl_ac")
        seed = c2.number_input("Total seed planted (lbs)", 0.0, step=50.0, key="pl_seed",
                               help="Seed lbs ÷ acres gives seeding rate — "
                                    "one of the few management variables we can model.")
        if acres > 0 and seed > 0:
            st.caption(f"→ Seeding rate: **{seed/acres:.1f} lbs/acre**")

        c3, c4 = st.columns(2)
        soil = c3.selectbox("Soil moisture at planting *",
                            ["", "dry", "adequate", "wet"], key="pl_soil",
                            help="Your 2025 notes suggest this matters a lot: the field "
                                 "'planted into moisture' yielded 867, the one 'planted "
                                 "into dry' yielded 122.")
        method = c4.selectbox("Planting method", ["", "drilled", "broadcast", "other"],
                              key="pl_method")
        c5, c6 = st.columns(2)
        variety = c5.text_input("Variety", key="pl_var")
        prev = c6.text_input("Previous crop", key="pl_prev")
        c7, c8 = st.columns(2)
        spacing = c7.number_input("Row spacing (in)", 0.0, step=1.0, key="pl_sp")
        exp = c8.number_input("Grower's years growing mung beans", 0, step=1, key="pl_exp")
        notes = st.text_area("Notes", key="pl_notes")

        if st.button("Save planting", type="primary", key="pl_save"):
            if acres <= 0 or not soil:
                st.error("Acres and soil moisture at planting are required.")
            else:
                ok, msg = db.upsert_season(dict(
                    field_id=fid, season_year=int(year),
                    planting_date=str(pdate), acres=acres,
                    seed_lbs=seed or None, variety=variety, previous_crop=prev,
                    soil_condition_planting=soil, planting_method=method,
                    row_spacing_in=spacing or None,
                    grower_years_experience=int(exp) if exp else None, notes=notes))
                (st.success if ok else st.error)(msg)


# ── Mid-season visit ───────────────────────────────────────────────────────
with tab_visit:
    st.subheader("Field visit")
    st.caption("Quick mid-season checkpoint — lets us check the moisture model "
               "against what the crop actually looked like, while the season is live.")
    fid = pick_field("v_fid")
    if fid:
        year = st.number_input("Season year *", 2020, 2100, YEAR_DEFAULT, key="v_yr")
        vdate = st.date_input("Visit date *", value=dt.date.today(), key="v_date")
        stage = st.selectbox("Growth stage",
                             ["", "emergence", "vegetative", "flowering",
                              "pod fill", "maturity"], key="v_stage")
        score = st.slider("Crop condition (1 poor → 5 excellent)", 1, 5, 3, key="v_score")
        vnotes = st.text_area("Observations", key="v_notes",
                              placeholder="Stand quality, weed pressure, moisture stress…")
        if st.button("Save visit", type="primary", key="v_save"):
            ok, msg = db.insert("visits", dict(
                field_id=fid, season_year=int(year), visit_date=str(vdate),
                growth_stage=stage, condition_score=int(score), notes=vnotes))
            (st.success if ok else st.error)(msg)


# ── Harvest ────────────────────────────────────────────────────────────────
with tab_harvest:
    st.subheader("Harvest record")
    st.caption("Adds to the existing planting record for this field-year.")
    fid = pick_field("h_fid")
    if fid:
        year = st.number_input("Season year *", 2020, 2100, YEAR_DEFAULT, key="h_yr")
        seasons = db.read("field_seasons")
        match = seasons[(seasons.field_id == fid) &
                        (seasons.season_year == int(year))] if len(seasons) else pd.DataFrame()
        if not len(match):
            st.warning("No planting record for this field-year yet. You can still save "
                       "harvest, but add the planting record so acres are captured.")
        acres_known = float(match.acres.iloc[0]) if len(match) and pd.notna(match.acres.iloc[0]) else None

        hdate = st.date_input("Harvest date", value=dt.date.today(), key="h_date")
        net = st.number_input("Net lbs harvested *", 0.0, step=100.0, key="h_net")
        clean = st.number_input("Cleanout %", 0.0, 100.0, step=0.5, key="h_clean")
        if net > 0 and acres_known:
            st.success(f"→ Yield: **{net/acres_known:.1f} lbs/acre** ({acres_known:.0f} ac)")
        hnotes = st.text_area("Harvest notes", key="h_notes")

        if st.button("Save harvest", type="primary", key="h_save"):
            if net <= 0:
                st.error("Net lbs is required.")
            else:
                ok, msg = db.upsert_season(dict(
                    field_id=fid, season_year=int(year), harvest_date=str(hdate),
                    net_lbs=net, cleanout_pct=clean or None, notes=hnotes))
                (st.success if ok else st.error)(msg)


# ── Data review / export ───────────────────────────────────────────────────
with tab_data:
    st.subheader("Logged data")
    f, s, v = db.read("fields"), db.read("field_seasons"), db.read("visits")
    st.metric("Fields registered", len(f))
    c1, c2 = st.columns(2)
    c1.metric("Season records", len(s))
    c2.metric("Visits logged", len(v))

    if len(s) and len(f):
        j = s.merge(f[["field_id", "grower_name", "location_name"]], on="field_id", how="left")
        if "net_lbs" in j and "acres" in j:
            j["lbs_per_acre"] = (j.net_lbs / j.acres).round(1)
        if "seed_lbs" in j and "acres" in j:
            j["seeding_rate"] = (j.seed_lbs / j.acres).round(1)
        st.dataframe(j, use_container_width=True, hide_index=True)

    for name, d in [("fields", f), ("field_seasons", s), ("visits", v)]:
        if len(d):
            st.download_button(f"Download {name}.csv", d.to_csv(index=False),
                               f"{name}.csv", "text/csv", key=f"dl_{name}")

    if len(f):
        st.markdown("**Registered field locations**")
        st.map(f[["lat", "lon"]].dropna(), zoom=7)
