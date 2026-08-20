"""
Clean Crop — Mung Bean Field Logger

Phone-friendly data collection for dryland mung bean fields. Separate from the
Arbol soil-moisture monitoring dashboard; this one is about building a season-
over-season panel: stable field IDs + GPS + planting/management + harvest.

The field is chosen once, at the top, and every tab works on that field — so a
grower standing in one place fills in planting, visits and harvest without
re-selecting anything.

Gated by a shared passcode (set `app_passcode` in Streamlit secrets).
"""

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

import db

st.set_page_config(page_title="Field Logger — Mung Beans",
                   page_icon="🌱", layout="centered")   # centered = better on phones


# ── Passcode gate ──────────────────────────────────────────────────────────
# Two roles, told apart by which passcode was typed:
#   collector — the entry tabs only
#   admin     — also sees the whole dataset, the export, and edit/delete
# If only `app_passcode` is configured it grants admin, so a single-passcode
# setup behaves exactly as it did before this split existed.
def _secret(name):
    try:
        return st.secrets[name]
    except (KeyError, FileNotFoundError):
        return None


def _gate():
    """Returns the caller's role, or None while they still need to sign in."""
    collector_pin = _secret("app_passcode")
    admin_pin = _secret("admin_passcode")

    if collector_pin is None and admin_pin is None:
        return "admin"                      # local dev, nothing configured

    if st.session_state.get("_role"):
        return st.session_state["_role"]

    st.title("🌱 Field Logger")
    st.caption("Enter the passcode and your name to continue.")
    pin = st.text_input("Passcode", type="password", key="gate_pin")
    who = st.text_input("Your name", key="gate_who",
                        placeholder="So we know who recorded each entry")
    if st.button("Enter", type="primary", key="gate_go"):
        if admin_pin is not None and pin == admin_pin:
            role = "admin"
        elif collector_pin is not None and pin == collector_pin:
            role = "collector" if admin_pin is not None else "admin"
        else:
            st.error("Incorrect passcode.")
            return None
        if not who.strip():
            st.error("Enter your name — every entry is stamped with who recorded it.")
            return None
        st.session_state["_role"] = role
        st.session_state["_who"] = who.strip()
        st.rerun()
    return None


ROLE = _gate()
if ROLE is None:
    st.stop()
WHO = st.session_state.get("_who", "")

st.title("🌱 Mung Bean Field Logger")
if WHO:
    st.caption(f"Signed in as **{WHO}**"
               + (" · reviewer" if ROLE == "admin" else "")
               + " — [sign out](?signout=1)")
    if st.query_params.get("signout"):
        for k in ("_role", "_who"):
            st.session_state.pop(k, None)
        st.query_params.clear()
        st.rerun()

if not db.using_supabase():
    st.warning(
        "**Local storage mode** — saving to CSV on this machine only. "
        "Configure `supabase_url` / `supabase_key` in secrets before field use, "
        "or submissions from different phones won't be shared.",
        icon="⚠️",
    )

# Dates come from Oklahoma's clock, not the server's. Streamlit Cloud runs in
# UTC, where "today" rolls over at 7pm local — so an evening entry after a day in
# the field would silently pre-fill tomorrow.
FARM_TZ = ZoneInfo("America/Chicago")


def today():
    return dt.datetime.now(FARM_TZ).date()


fields_df = db.read("fields")
YEAR_DEFAULT = today().year
MAP_HOME = (36.0, -98.0)   # where the map opens before a field is pinned (NW Oklahoma)


# ── The working field, chosen once for every tab ───────────────────────────
def field_label(r):
    farm = f" · {r.farm_name}" if getattr(r, "farm_name", None) and pd.notna(r.farm_name) else ""
    return f"{r.field_id} — {r.grower_name}{farm}"


active_fid = None
if len(fields_df):
    opts = {field_label(r): r.field_id for r in fields_df.itertuples()}
    # Default to the field most recently registered or worked on, so saving a
    # field and moving to Planting carries the selection across with no re-picking.
    remembered = st.session_state.get("active_fid")
    labels = list(opts)
    idx = next((i for i, lb in enumerate(labels) if opts[lb] == remembered), 0)
    chosen = st.selectbox("Working field", labels, index=idx, key="field_picker")
    active_fid = opts[chosen]
    st.session_state["active_fid"] = active_fid

season_year = st.number_input("Season year", 2020, 2100, YEAR_DEFAULT, key="season_year")
st.divider()

# The Data tab holds the whole dataset and the export, so collectors don't get
# it — they see only the tabs they enter through.
_labels = ["📍 New Field", "🌱 Planting", "🔍 Visits", "🚜 Harvest"]
if ROLE == "admin":
    _labels += ["📋 Data", "⚙️ Manage"]
_tabs = st.tabs(_labels)
tab_new, tab_plant, tab_visit, tab_harvest = _tabs[:4]
tab_data, tab_manage = (_tabs[4], _tabs[5]) if ROLE == "admin" else (None, None)


def needs_field():
    """Shared guard for the tabs that operate on an already-registered field."""
    if active_fid is None:
        st.info("No fields registered yet — add one on the **New Field** tab first.")
        return True
    return False


def season_row(fid, year):
    """The field_seasons row for this field-year, or None."""
    s = db.read("field_seasons")
    if not len(s):
        return None
    m = s[(s.field_id == fid) & (s.season_year == int(year))]
    return m.iloc[0] if len(m) else None


def prior(row, col):
    """Previously saved value for a column, or None — used to prefill forms."""
    if row is None or col not in row or pd.isna(row[col]):
        return None
    return row[col]


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

    st.markdown("**Location**")
    st.caption("Pin it on the map while standing in the field, **or** type coordinates "
               "you already have — both work, use whichever suits.")
    lat = st.session_state.get("nf_lat")
    lon = st.session_state.get("nf_lon")
    has_pin = lat is not None and lon is not None

    try:
        import folium
        from folium.plugins import LocateControl
        from streamlit_folium import st_folium

        fmap = folium.Map(location=[lat, lon] if has_pin else list(MAP_HOME),
                          zoom_start=16 if has_pin else 8,
                          tiles="Esri.WorldImagery", attr="Esri")
        # "Find me" button — centres the map on the phone's GPS. Requires HTTPS:
        # browsers block geolocation on insecure origins, so this does nothing on
        # a plain-http LAN address and only works once the app is deployed.
        LocateControl(auto_start=True, flyTo=True,
                      strings={"title": "Find my location"}).add_to(fmap)
        if has_pin:
            folium.Marker([lat, lon], tooltip="This field").add_to(fmap)

        st.caption("Tap **Find my location** (the ⌖ button), then tap the map where "
                   "you're standing to drop the pin.")
        clicked = st_folium(fmap, height=340, width=None,
                            returned_objects=["last_clicked"], key="nf_map")
        if clicked and clicked.get("last_clicked"):
            cl = clicked["last_clicked"]
            if (round(cl["lat"], 6), round(cl["lng"], 6)) != (lat, lon):
                st.info(f"Tapped {cl['lat']:.6f}, {cl['lng']:.6f}")
                if st.button("Use this location", key="nf_usepin"):
                    st.session_state["nf_lat"] = round(cl["lat"], 6)
                    st.session_state["nf_lon"] = round(cl["lng"], 6)
                    st.rerun()
    except Exception as e:
        st.error(f"Map unavailable ({e}). Use manual entry below.")

    if has_pin:
        st.success(f"📍 Pinned at **{lat:.6f}, {lon:.6f}**")
    else:
        st.caption("No location pinned yet.")

    # A co-equal path to the map, not a fallback: fields recorded before this app
    # existed already have coordinates, and back-filling them by tapping a map is
    # both slower and less accurate than pasting the numbers that were measured.
    st.markdown("**Type coordinates**")
    h1, h2 = st.columns(2)
    m_lat = h1.number_input("Latitude", value=None, format="%.6f", key="nf_mlat",
                            placeholder="35.972976")
    m_lon = h2.number_input("Longitude", value=None, format="%.6f", key="nf_mlon",
                            placeholder="-98.118345")
    if st.button("Use these coordinates", key="nf_usemanual"):
        if m_lat is None or m_lon is None:
            st.error("Enter both latitude and longitude.")
        elif not (-90 <= m_lat <= 90 and -180 <= m_lon <= 180):
            st.error("Those aren't valid coordinates — latitude is −90 to 90, "
                     "longitude −180 to 180.")
        elif m_lon > 0:
            # Everything in this program is in Oklahoma; a positive longitude is
            # the eastern hemisphere and almost always a dropped minus sign.
            st.error(f"Longitude {m_lon} is east of Greenwich — did you mean "
                     f"−{m_lon}? Oklahoma longitudes are negative.")
        else:
            st.session_state["nf_lat"] = round(m_lat, 6)
            st.session_state["nf_lon"] = round(m_lon, 6)
            st.rerun()

    water = st.radio("Water *", ["Dryland", "Irrigated"], index=None,
                     horizontal=True, key="nf_water")

    fid_hint = db.suggest_field_id(grower, fields_df)
    fid = st.text_input("Field ID *", key="nf_fid", placeholder=fid_hint,
                        help="Permanent identifier, reused every season. "
                             "Convention: grower surname + number, e.g. DOE-01.")
    st.caption(f"Suggested next ID for this grower: **{fid_hint}** — type it in.")

    nf_notes = st.text_area("Notes", key="nf_notes",
                            placeholder="Anything notable about this ground — "
                                        "creek bottom, drainage, past problems…")

    # Flag coordinates that land on an already-registered field. Compared with a
    # distance tolerance (1e-5 deg ~ 1 m) rather than rounded equality: pandas
    # rounds half-to-even and Python's round() does not, so they disagree on .5.
    dup_loc = None
    if has_pin and len(fields_df):
        close = ((fields_df.lat - lat).abs() < 1e-5) & ((fields_df.lon - lon).abs() < 1e-5)
        if close.any():
            dup_loc = str(fields_df.loc[close, "field_id"].iloc[0])

    allow_dup = False
    if dup_loc:
        st.warning(f"That pin is within about a metre of **{dup_loc}**. "
                   f"If this is a different field, re-pin it.")
        allow_dup = st.checkbox("Save anyway — genuinely two fields at one point",
                                key="nf_allow_dup_loc")

    if st.button("Save field", type="primary", key="nf_save"):
        if not (grower and fid):
            st.error("Grower name and Field ID are required.")
        elif not has_pin:
            st.error("Pin the field on the map first, or enter coordinates by hand.")
        elif water is None:
            st.error("Choose Dryland or Irrigated.")
        elif len(fields_df) and fid in set(fields_df.field_id.astype(str)):
            st.error(f"Field ID '{fid}' already exists — pick a different one.")
        elif dup_loc and not allow_dup:
            st.error(f"That pin matches {dup_loc}. Re-pin the field, or tick the box "
                     f"above to save both at the same point.")
        else:
            ok, msg = db.insert("fields", dict(
                field_id=fid, grower_name=grower, farm_name=farm,
                lat=lat, lon=lon, irrigated=(water == "Irrigated"),
                notes=nf_notes, recorded_by=WHO))
            if not ok:
                st.error(msg)
            else:
                st.cache_data.clear()
                # Keep the grower and farm — the next field is usually the same
                # grower — but clear everything specific to the field just saved.
                # Popping resets a widget to its default; assigning to a widget key
                # after the widget exists raises, so these must be popped, not set.
                for k in ("nf_lat", "nf_lon", "nf_notes", "nf_map", "nf_water",
                          "nf_fid", "nf_mlat", "nf_mlon", "nf_allow_dup_loc"):
                    st.session_state.pop(k, None)
                st.session_state["active_fid"] = fid       # carry into the other tabs
                st.session_state.pop("field_picker", None)
                st.session_state["_just_saved"] = fid
                st.rerun()

    if st.session_state.get("_just_saved"):
        st.success(f"Saved **{st.session_state.pop('_just_saved')}** and made it the "
                   "working field. Planting, Visits and Harvest now point at it.")


# ── Planting ───────────────────────────────────────────────────────────────
with tab_plant:
    st.subheader("Planting record")
    if not needs_field():
        row = season_row(active_fid, season_year)
        st.caption(f"**{active_fid}** · {int(season_year)}"
                   + ("  — editing the saved record" if row is not None else ""))

        pdate = st.date_input("Planting date *",
                              value=pd.to_datetime(prior(row, "planting_date")).date()
                              if prior(row, "planting_date") else today(),
                              key="pl_date")
        c1, c2 = st.columns(2)
        acres = c1.number_input("Acres planted *", 0.0, value=prior(row, "acres"),
                                step=10.0, key="pl_ac")
        rate = c2.number_input("Seed planted (lbs/acre) *", 0.0,
                               value=prior(row, "seed_lbs_per_acre"),
                               step=1.0, key="pl_rate")
        if acres and rate:
            st.caption(f"→ Total seed: **{acres * rate:,.0f} lbs** over {acres:,.0f} acres")

        soil_opts = ["dry", "adequate", "wet"]
        soil_prior = prior(row, "soil_condition_planting")
        soil = st.radio("Soil moisture at planting *", soil_opts,
                        index=soil_opts.index(soil_prior) if soil_prior in soil_opts else None,
                        horizontal=True, key="pl_soil",
                        help="Your 2025 notes suggest this matters a lot: the field "
                             "'planted into moisture' yielded 867 lbs/ac, the one "
                             "'planted into dry' yielded 122.")

        with st.expander("Optional details"):
            m_opts = ["drilled", "broadcast", "other"]
            m_prior = prior(row, "planting_method")
            method = st.radio("Planting method", m_opts,
                              index=m_opts.index(m_prior) if m_prior in m_opts else None,
                              horizontal=True, key="pl_method")
            spacing = st.number_input("Row spacing (in)", 0.0,
                                      value=prior(row, "row_spacing_in"),
                                      step=1.0, key="pl_sp")

        notes = st.text_area("Planting notes", value=prior(row, "planting_notes") or "",
                             key="pl_notes")

        if st.button("Save planting", type="primary", key="pl_save"):
            if not acres or not rate or not soil:
                st.error("Acres, seed lbs/acre, and soil moisture at planting are required.")
            else:
                ok, msg = db.upsert_season(dict(
                    field_id=active_fid, season_year=int(season_year),
                    planting_date=str(pdate), acres=acres, seed_lbs_per_acre=rate,
                    soil_condition_planting=soil, planting_method=method,
                    row_spacing_in=spacing, planting_notes=notes,
                    planting_by=WHO))
                if ok:
                    st.success("Planting saved.")
                    st.cache_data.clear()
                else:
                    st.error(msg)


# ── Mid-season visits (as many as the grower wants) ────────────────────────
with tab_visit:
    st.subheader("Field visits")
    if not needs_field():
        st.caption(f"**{active_fid}** · {int(season_year)}")

        visits = db.read("visits")
        mine = visits[(visits.field_id == active_fid) &
                      (visits.season_year == int(season_year))] if len(visits) else pd.DataFrame()

        if len(mine):
            st.markdown(f"**{len(mine)} visit(s) logged this season**")
            for r in mine.sort_values("visit_date", ascending=False).itertuples():
                stage = f" · {r.growth_stage}" if pd.notna(r.growth_stage) and r.growth_stage else ""
                score = f" · condition {int(r.condition_score)}/5" if pd.notna(r.condition_score) else ""
                with st.container(border=True):
                    st.markdown(f"**{r.visit_date}**{stage}{score}")
                    if pd.notna(r.notes) and r.notes:
                        st.write(r.notes)
        else:
            st.caption("No visits logged for this field-year yet.")

        st.markdown("**Add a visit**")
        vdate = st.date_input("Visit date *", value=today(), key="v_date")
        stage = st.selectbox("Growth stage",
                             ["", "emergence", "vegetative", "flowering",
                              "pod fill", "maturity"], key="v_stage")
        score = st.slider("Crop condition (1 poor → 5 excellent)", 1, 5, 3, key="v_score")
        vnotes = st.text_area("Observations", key="v_notes",
                              placeholder="Stand quality, weed pressure, moisture stress…")

        if st.button("Add this visit", type="primary", key="v_save"):
            if not (vnotes or stage):
                st.error("Add an observation or a growth stage — "
                         "a visit with neither records nothing.")
            else:
                ok, msg = db.insert("visits", dict(
                    field_id=active_fid, season_year=int(season_year),
                    visit_date=str(vdate), growth_stage=stage,
                    condition_score=int(score), notes=vnotes,
                    recorded_by=WHO))
                if ok:
                    st.cache_data.clear()
                    # Clear the entry boxes so the next visit starts blank.
                    for k in ("v_notes", "v_stage", "v_score"):
                        st.session_state.pop(k, None)
                    st.rerun()
                else:
                    st.error(msg)


# ── Harvest ────────────────────────────────────────────────────────────────
with tab_harvest:
    st.subheader("Harvest record")
    if not needs_field():
        row = season_row(active_fid, season_year)
        st.caption(f"**{active_fid}** · {int(season_year)}")

        acres_known = prior(row, "acres")
        if acres_known is None:
            st.warning("No planting record for this field-year yet. You can still save "
                       "the harvest, but add the planting record so acres are captured.")

        hdate = st.date_input("Harvest date",
                              value=pd.to_datetime(prior(row, "harvest_date")).date()
                              if prior(row, "harvest_date") else today(),
                              key="h_date")
        yield_pa = st.number_input("Harvested (lbs/acre) *", 0.0,
                                   value=prior(row, "yield_lbs_per_acre"),
                                   step=10.0, key="h_yield")
        if yield_pa and acres_known:
            st.success(f"→ Total: **{yield_pa * acres_known:,.0f} lbs** "
                       f"over {acres_known:,.0f} acres")
        hnotes = st.text_area("Harvest notes", value=prior(row, "harvest_notes") or "",
                              key="h_notes")

        if st.button("Save harvest", type="primary", key="h_save"):
            if not yield_pa:
                st.error("Harvested lbs/acre is required.")
            else:
                ok, msg = db.upsert_season(dict(
                    field_id=active_fid, season_year=int(season_year),
                    harvest_date=str(hdate), yield_lbs_per_acre=yield_pa,
                    harvest_notes=hnotes, harvest_by=WHO))
                if ok:
                    st.success("Harvest saved.")
                    st.cache_data.clear()
                else:
                    st.error(msg)


# ── Data review / export ───────────────────────────────────────────────────
def render_data():
    st.subheader("Logged data")
    f, s, v = db.read("fields"), db.read("field_seasons"), db.read("visits")
    c1, c2, c3 = st.columns(3)
    c1.metric("Fields", len(f))
    c2.metric("Season records", len(s))
    c3.metric("Visits", len(v))

    combined = pd.DataFrame()
    if len(s) and len(f):
        combined = s.merge(f[["field_id", "grower_name", "farm_name", "lat", "lon",
                              "irrigated", "recorded_by"]], on="field_id", how="left")
        if "yield_lbs_per_acre" in combined and "acres" in combined:
            combined["total_lbs"] = (combined.yield_lbs_per_acre * combined.acres).round(0)
        st.dataframe(combined, width="stretch", hide_index=True)
    elif len(f):
        st.dataframe(f, width="stretch", hide_index=True)

    st.markdown("**Export**")
    if len(combined):
        st.download_button("⬇️ Everything (one sheet)", combined.to_csv(index=False),
                           "field_logger_combined.csv", "text/csv",
                           type="primary", key="dl_combined")
    for name, d in [("fields", f), ("field_seasons", s), ("visits", v)]:
        if len(d):
            st.download_button(f"⬇️ {name}.csv", d.to_csv(index=False),
                               f"{name}.csv", "text/csv", key=f"dl_{name}")

    if len(f):
        st.markdown("**Registered field locations**")
        st.map(f[["lat", "lon"]].dropna(), zoom=7)

    # ── Regional soil moisture ─────────────────────────────────────────────
    sm_path = Path(__file__).parent / "soil_moisture.parquet"
    if sm_path.exists():
        st.divider()
        st.markdown("**Soil moisture this season**")
        sm = pd.read_parquet(sm_path)

        latest = sm.dropna(subset=["vswc"]).iloc[-1]
        delta = (latest.vswc - latest.normal_med) * 100
        m1, m2 = st.columns(2)
        m1.metric(f"Latest ({latest.date:%d %b})", f"{latest.vswc * 100:.1f}%",
                  f"{delta:+.1f} pts vs normal")
        m2.metric("10-year normal", f"{latest.normal_med * 100:.1f}%")

        # .values, not the Series: passing Series with a 0..n index alongside a
        # datetime index makes pandas align on the old labels and silently
        # produce an all-NaN frame, which renders as an empty chart.
        chart = pd.DataFrame({
            "This season": (sm.vswc * 100).values,
            "Normal (median)": (sm.normal_med * 100).values,
            "Normal (wet, 90th)": (sm.normal_high * 100).values,
            "Normal (dry, 10th)": (sm.normal_low * 100).values,
        }, index=pd.DatetimeIndex(sm.date, name="date"))
        st.line_chart(chart, height=280)

        st.caption(
            f"Volumetric soil water, 7–28 cm (ERA5), at grid cell "
            f"{latest.grid_lat:.2f}, {latest.grid_lon:.2f}, against the 2015–2024 "
            f"normal for the same calendar days. **One regional curve, not per "
            f"field** — ERA5's grid is about 28 km, so every field in the program "
            f"sits in this cell or one beside it. ERA5 publishes 5–7 days behind, "
            f"so the last few days of any series are provisional."
        )
        st.download_button("⬇️ soil_moisture.csv", sm.to_csv(index=False),
                           "soil_moisture.csv", "text/csv", key="dl_sm")


# ── Manage: correct or remove a registered field (reviewer only) ───────────
def render_manage():
    st.subheader("Edit or remove a field")
    if active_fid is None:
        st.info("No fields registered yet.")
    else:
        cur = fields_df[fields_df.field_id == active_fid].iloc[0]
        st.caption(f"Editing **{active_fid}** — chosen with the working-field "
                   "picker at the top of the page.")

        e_fid = st.text_input("Field ID", value=str(cur.field_id), key="ed_fid",
                              help="Renaming carries the planting, visit and harvest "
                                   "records with it, so the season-over-season link "
                                   "survives.")
        c1, c2 = st.columns(2)
        e_grower = c1.text_input("Grower name", value=str(cur.grower_name), key="ed_grower")
        e_farm = c2.text_input("Farm name",
                               value="" if pd.isna(cur.farm_name) else str(cur.farm_name),
                               key="ed_farm")
        c3, c4 = st.columns(2)
        e_lat = c3.number_input("Latitude", value=float(cur.lat), format="%.6f", key="ed_lat")
        e_lon = c4.number_input("Longitude", value=float(cur.lon), format="%.6f", key="ed_lon")
        e_water = st.radio("Water", ["Dryland", "Irrigated"],
                           index=1 if bool(cur.irrigated) else 0,
                           horizontal=True, key="ed_water")
        e_notes = st.text_area("Notes",
                               value="" if pd.isna(cur.notes) else str(cur.notes),
                               key="ed_notes")

        if st.button("Save changes", type="primary", key="ed_save"):
            clash = (e_fid != active_fid and len(fields_df)
                     and e_fid in set(fields_df.field_id.astype(str)))
            if not (e_fid and e_grower):
                st.error("Field ID and grower name can't be blank.")
            elif clash:
                st.error(f"Field ID '{e_fid}' is already taken.")
            else:
                ok, msg = db.update_field(active_fid, dict(
                    field_id=e_fid, grower_name=e_grower, farm_name=e_farm,
                    lat=e_lat, lon=e_lon, irrigated=(e_water == "Irrigated"),
                    notes=e_notes))
                if ok:
                    st.cache_data.clear()
                    st.session_state["active_fid"] = e_fid
                    st.session_state.pop("field_picker", None)
                    st.success(f"Saved. {'Renamed to ' + e_fid + '.' if e_fid != active_fid else ''}")
                    st.rerun()
                else:
                    st.error(msg)

        st.divider()
        st.markdown("**Delete this field**")
        seasons_n = len(db.read("field_seasons").query("field_id == @active_fid")) \
            if len(db.read("field_seasons")) else 0
        visits_n = len(db.read("visits").query("field_id == @active_fid")) \
            if len(db.read("visits")) else 0
        st.warning(f"Deleting **{active_fid}** also removes **{seasons_n} season "
                   f"record(s)** and **{visits_n} visit(s)**. This cannot be undone.")
        confirm = st.text_input(f"Type {active_fid} to confirm", key="del_confirm")
        if st.button("Delete field", key="del_go", disabled=confirm != active_fid):
            ok, msg, counts = db.delete_field(active_fid)
            if ok:
                st.cache_data.clear()
                for k in ("active_fid", "field_picker", "del_confirm"):
                    st.session_state.pop(k, None)
                st.success(f"{msg} Removed {counts['field_seasons']} season record(s) "
                           f"and {counts['visits']} visit(s).")
                st.rerun()
            else:
                st.error(msg)


# Rendered only for the reviewer. Guarding here rather than inside the tab keeps
# the whole dataset out of a collector's page, not merely out of their tab strip.
if ROLE == "admin":
    with tab_data:
        render_data()
    with tab_manage:
        render_manage()
