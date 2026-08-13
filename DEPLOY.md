# Deploying the Field Logger

Two independent pieces. **Supabase is the one that matters** — without it every
phone writes its own private CSV and there is no merged export. Hosting only
decides where the app runs and whether the GPS button works.

---

## 1. Supabase — the shared database

Free tier is far more than four users need.

1. Create a project at <https://supabase.com> (choose a region near Oklahoma —
   `us-east-1` is fine).
2. Open **SQL Editor → New query**, paste the whole of [`schema.sql`](schema.sql),
   and run it. That creates `fields`, `field_seasons`, `visits` and their access
   policies.
3. Go to **Project Settings → API** and copy two values:
   - **Project URL** → `supabase_url`
   - **anon / public** key → `supabase_key`
4. Locally: copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`
   and paste them in, along with the two passcodes (see step 3). That file is
   gitignored — it must never be committed.

Restart the app. The orange "Local storage mode" banner disappears when it is
talking to Supabase; that banner is the check that this worked.

**Migrating what is already on this machine:** the local CSVs in `local_data/`
can be imported through Supabase's **Table Editor → Insert → Import data from
CSV**. Do `fields` first — the other two reference it.

---

## 2. Hosting — an HTTPS address for the phones

HTTPS is not cosmetic here. Browsers block geolocation on insecure origins, so
**the "Find my location" button cannot work until the app is served over HTTPS.**
`localhost` is exempt, which is why it works on the dev machine and nowhere else.

### GitHub first

Streamlit Cloud deploys from a repo, so the code has to be pushed:

```bash
gh repo create clean-crop-field-logger --private --source=. --remote=origin --push
```

### Streamlit Community Cloud

1. <https://share.streamlit.io> → **New app**, point it at the repo, main file
   `app.py`.
2. **Advanced settings → Secrets**: paste the same TOML as your local
   `secrets.toml` (app_passcode, supabase_url, supabase_key).
3. Deploy.

Known risk: the sibling `clean_crop_monitor` app is currently stuck on this
platform — hangs "in the oven" with empty logs, a recurring Cloud
infrastructure bug rather than a code fault. This app is much lighter (no CDS
pulls, no NetCDF, no heavy scientific stack), so it has a better chance. If it
hangs the same way, move to Render — the app itself needs no changes.

### If Streamlit Cloud fails: Render

New **Web Service** from the repo, ~$7/mo:

- Build: `pip install -r requirements.txt`
- Start: `streamlit run app.py --server.port $PORT --server.address 0.0.0.0`
- Add the same three secrets as environment variables.

No sleeping, no disk wipe, no one-private-app cap.

---

## 3. Giving it to the four users

There are two passcodes, and which one someone types decides what they see:

| Passcode | Who | Gets |
|---|---|---|
| `app_passcode` | the people collecting | New Field, Planting, Visits, Harvest |
| `admin_passcode` | you | the above, plus Data (export) and Manage (edit/delete) |

**Send the collectors `app_passcode` only.** Keep `admin_passcode` to yourself.
Everyone types their name at sign-in, and it is stamped on every row they enter.

Nothing to install — it is a web page they can add to the phone home screen
(Safari: Share → Add to Home Screen) so it opens like an app.

Worth being clear-eyed about: this split is a **UI convenience, not a security
boundary.** Both passcodes talk to Supabase with the same anon key, so someone
determined who pulled that key out of the page could read the tables directly.
That is an acceptable trade for four colleagues logging agronomic data — but do
not put anything genuinely sensitive in here on the strength of it.

---

## Refreshing the soil-moisture chart

`soil_moisture.parquet` is committed on purpose so the deployed app renders on a
cold start without CDS credentials. It does not update itself. To extend it:

1. Refresh the raw ERA5 data in the sibling monitoring project (it owns the CDS
   pull, and ERA5 publishes 5–7 days behind).
2. Re-run `python3 build_soil_moisture.py` here.
3. Commit the regenerated parquet.

Current coverage is 30 Jun – 31 Jul 2026, because that is the risk period the
monitoring project fetches. A planting-to-harvest view needs a wider pull there
first.
