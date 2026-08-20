# Field Logger — project context

Phone-first data collection for **2026 dryland mung bean fields**. Separate from
`../clean_crop_monitor/` (Arbol soil-moisture insurance mirror) and
`../yield_model/` (yield prediction), though it borrows ERA5 data from the first.

## Why it exists

Historical mung bean data could not support a yield model, for one reason above
all others: **no stable field identity across seasons.** Every year was a fresh
spreadsheet with fields named differently, so nothing could be tracked over time.

Hence the central design rule: `fields` holds identity that never changes, and
`field_seasons` holds one row per field per year. `field_id` is invented once
and reused forever. Everything else is secondary to preserving that link.

## Shape

| File | Purpose |
|---|---|
| `app.py` | The whole UI — 5 tabs, working field chosen once at the top |
| `db.py` | Storage: Supabase when configured, local CSV fallback otherwise |
| `schema.sql` | Run once in the Supabase SQL editor |
| `build_soil_moisture.py` | Precomputes `soil_moisture.parquet`; run locally, commit the result |
| `DEPLOY.md` | Supabase + hosting steps |

The field and season year are selected **once above the tabs**, and Planting /
Visits / Harvest all act on that selection — a grower standing in one spot fills
everything in without re-picking. Saving a new field makes it active immediately.

## Two roles

Told apart by which passcode was typed at the gate:

- **collector** (`app_passcode`) — the four entry tabs only.
- **admin** (`admin_passcode`) — also Data (whole dataset + export) and Manage
  (edit / rename / delete a field).

Configure only `app_passcode` and it grants admin, so a single-passcode setup
behaves as it did before the split. The guard is `if ROLE == "admin"` around the
*render calls*, not inside the tabs — a collector's page never contains the data
at all, rather than merely hiding a tab.

Everyone gives their name at sign-in, stamped onto every row: `recorded_by` on
fields and visits, and `planting_by` / `harvest_by` on seasons, since those two
halves are routinely entered by different people on different days.

## Decisions already made (don't relitigate)

- **Location is lat/lon only.** Nearest town and county were deliberately dropped —
  coordinates already say where a field is, and town names invite the
  centroid-of-a-parking-lot problem.
- **Both map-pin and typed coordinates are first-class.** Typed entry is not a
  fallback: fields recorded before this app existed already have coordinates, and
  pasting them is more accurate than re-tapping a map.
- **Field ID is never pre-filled.** The suggestion (grower surname + number,
  `DOE-01`) is placeholder text the user types over. It is the one value that must
  survive to 2027, so it gets a deliberate keystroke.
- **Planting captures seed lbs/acre, harvest captures lbs/acre harvested** — both
  entered directly, totals derived. Variety, previous crop and years-of-experience
  were cut as not worth the phone keystrokes; soil condition at planting was kept
  (2025: "planted into moisture" 867 lbs/ac vs "planted into dry" 122).
- **Water is a forced choice**, no default, so dryland is never assumed silently.
- **`planting_notes` and `harvest_notes` are separate columns.** A single shared
  `notes` column meant saving harvest destroyed the planting note.
- **No Excel template.** Superseded by the app plus its CSV export.

## Gotchas

- **Geolocation needs HTTPS.** Browsers block it on insecure origins, so the
  "Find my location" button is dead on a plain-http LAN address and works only on
  `localhost` or a deployed HTTPS URL. This makes deployment a prerequisite for
  the core premise, not a finishing touch.
- **The CSV fallback is single-machine.** Fine for solo testing, useless for four
  people — each writes an isolated file. Supabase is what makes one merged export
  possible.
- **Streamlit widget state:** a keyed widget's `session_state` value beats
  `value=`, so a suggestion passed as `value=` silently freezes at its first
  render. And you cannot *assign* to a widget key after the widget exists — pop it
  to reset it.
- **`requirements.txt` uses loose lower bounds on purpose.** Exact pins from a
  local Python 3.13 env are often unresolvable on the host — that is what stalls
  Streamlit Cloud builds. Learned on `clean_crop_monitor`.
- **ERA5 is ~28 km.** Every field in this program lands in one or two grid cells,
  so soil moisture is shown as **one regional curve, not per field**. Confirmed on
  the monitoring project, where 5 of 7 insured locations collapsed into 2 cells.
  ERA5-Land was tested there and is worse, not finer. Publication lag is 5–7 days.
- **Building a DataFrame from Series with a different index** silently produces
  all-NaN (an empty chart). Pass `.values` when supplying your own index.
- **PostgREST sends whole numbers as `500`, not `500.0`**, so pandas types the
  column int64 — and `st.number_input` refuses an int value against a float step.
  Cast numeric prefills with `prior_num`. Local fixtures hide this, because
  `set_value` hands over real floats.
- **An all-null column reads back as object dtype**, so arithmetic on it cannot be
  rounded. `pd.to_numeric(..., errors="coerce")` before any maths on a column that
  is empty until harvest.
- **Geolocation must be requested on a tap, not on page load.** A gestureless
  request gets denied outright with no permission prompt, which surfaces as
  "user denied geolocation" before the user has touched anything.
- **Checking the deployed app with a bare `curl` is misleading.** Streamlit's
  session handshake 303s in a loop without a cookie jar, which looks exactly like
  an auth wall or a dead container. Use `-c/-b` before concluding anything.

## Live

- **App:** <https://mungbeanlog.streamlit.app> — Streamlit Community Cloud, public
  (the one free private slot is taken), gated by passcode. Deploys from `main` on
  push, though the auto-deploy hook was flaky after the repo moved orgs; a manual
  **Reboot app** forces a pull.
- **Repo:** `clean-crop/clean-crop-field-logger`, public. `soil_moisture.parquet`
  is committed on purpose so a cold start renders without CDS credentials.
- **Database:** Supabase, schema and RLS applied. A GitHub Action pings all three
  tables every 3 days, because the free tier pauses a project after 7 days idle
  and a quiet week mid-season is normal.
- Verified end to end on a third party's phone and desktop: public access, both
  passcodes, GPS over HTTPS.

## Open

- **Role separation is enforced in app code, not in the database.** Streamlit runs
  server-side and never ships `st.secrets` to the browser, so a collector cannot
  extract the anon key from the page — the split holds against ordinary use. What
  it is not: a database-level boundary. Anyone who obtains the key by other means
  bypasses the passcode entirely, because RLS grants `anon` full access to all
  three tables. Real separation would need Supabase Auth and per-role policies.
- Recalling field IDs in the field in 2027 is unsolved beyond the working-field
  picker; deferred deliberately while 2026 collection is the focus.
- `soil_moisture.parquet` covers only 30 Jun – 31 Jul 2026, bounded by what the
  monitoring project fetches.

Run locally: `streamlit run app.py --server.port 8505`
