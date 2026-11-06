# V2 Retail — Customer App

A shopper-facing portal for V2's CRM: a customer signs in with the mobile number
they give at the billing counter and sees **their real bills, straight from
Snowflake** — every line item, what they saved, their V2 VIP Club card and a
breakdown of how they shop.

Built 05-Nov-2026 from the static prototype
`V2_Retail_Customer_App_Responsive.html` (kept in this folder for reference).
The prototype's hard-coded demo bills are gone; nothing on screen is invented.

```
run_app.bat          ->  http://127.0.0.1:5017
```

## What it does

| Screen | What the shopper gets |
|---|---|
| **Login** | Mobile → 6-digit OTP. The number must have at least one V2 bill, otherwise sign-in is refused. |
| **Overview** | Lifetime spend (net of returns), total savings, items bought, average bill, VIP card status or an upsell with their own numbers. Nudges from the CRM team appear as a banner here. |
| **Purchase history** | Every bill ever, searchable by bill number / store / item, filterable by period and store, with CSV export. |
| **Virtual bill** | A reproduction of the printed till slip — torn paper edges, V2 logo, monospace, every line with its colour, size and article number, struck-through MRP where a discount applied. Print / Save as PDF. Returns render as refund receipts. |
| **Offers** | Every running offer with its mechanic and terms — Bill Buster, Spin The Wheel, Selfie, Up to 70% Off, V2 VIP Club, festival gift slabs, birthday and anniversary. Maintained in the admin panel. |
| **Find my store** | All 439 trading stores, searchable by name / city / state / PIN, with one-tap Google Maps directions. "Stores near me" sorts by real distance from the shopper. |
| **My shopping** | Monthly spend (12-month bar chart), spend by category, stores visited, shopping rhythm. |
| **V2 VIP Club** | Tier, validity, card history, what the card has saved vs its fee, and the tier ladder. |
| **My details** | Name, email, DOB, anniversary, preferred store, marketing consent — written to Snowflake. |

It is an installable **PWA**: manifest + service worker + icon set, so a shopper
can add it to their home screen and it opens full-screen like a native app, with
their last-loaded bills readable offline.

**To deploy it, read `DEPLOY.md`.** `python serve.py --check` validates the
whole configuration and refuses to start anything a customer shouldn't see.

## Two rules that shape what customers see

**Purchases are shown from 01-Apr-2025 onwards** (`purchase_cutoff` in
`config.json`). Anything older is invisible everywhere — history, totals,
savings, insights, CSV and receipts.

The cutoff gates *what is shown*, never *who may sign in*. 18.8M of 46.6M
customers have no bill after that date; refusing them a login would tell 40% of
the base that V2 has never heard of them. They sign in and get a plain message
naming the window instead.

**A sign-in lasts 60 days and renews on every visit.** Open the app even once a
month and you are never asked for an OTP again; disappear for two months and you
get one. The token lives in localStorage and travels as a bearer header — no
cookies, so no CSRF surface. Logging out also clears the service worker's cached
API responses, so a shared handset cannot leak the previous person's bills.

## Where the data comes from

Everything factual is Snowflake, per the standing rule that Snowflake is the
source for all reporting.

| Object | Used for |
|---|---|
| `V2RETAIL.CDP_WORK.CRM_SALES_LIVE_UC` | bill lines (mirror history + the same-day Axapta slice) |
| `V2RETAIL.GOLD.DIM_STORE_V2` + `RETAIL_HUB.STORES` + `SILVER.DIM_STORE_V2` | store name / city / state |
| `V2RETAIL.SILVER.DIM_ARTICLE_V2` | article description + material group |
| `V2RETAIL.CDP_WORK.TBL_ARTICLE` | major category + division |
| `V2RETAIL.CDP_WORK.ARTMASTER_EAN` | product label fallback |
| `V2RETAIL.CDP_WORK.CUSTOMER_PROFILE` | **written by this app** — the details shoppers enter |
| `V2RETAIL.CDP_WORK.APP_OFFER` / `APP_OFFER_STORE` | **written by this app** — the offer catalogue |
| `V2RETAIL.CDP_WORK.APP_STORE_LOCATION` | **written by this app** — THE store list, from the uploaded sheet |
| `V2RETAIL.CDP_WORK.APP_NUDGE` / `APP_NUDGE_READ` | **written by this app** — nudges and who opened them |

Connection is the shared `E:\ANAND PRAKASH\AUTOMATION\sf_helper.py`
(RSA key-pair JWT, `RAJATVERMA` / `DATA_PLATFORM_ADMIN` / `COMPUTE_WH`).

The **live** view is used rather than the firm T-1 mirror, because the mirror has
been lagging (it sat at 2026-10-18 when this was built, while live reached
2026-11-05). History is byte-identical to the mirror either way; only the newest
day or two is indicative. The header chip states the data-through date.

## The customer master this app creates

Snowflake knows a mobile number bought things; it has never known **who** that
number is. There is no customer master anywhere in the warehouse (CDP_Mine hit
the same wall and keeps a 7k-row contact snapshot for it).

So this app creates one. What a shopper enters under **My details** is written
to **`V2RETAIL.CDP_WORK.CUSTOMER_PROFILE`** — one row per mobile, maintained by
`MERGE`, so it becomes real CRM data (campaign targeting, birthday and
anniversary triggers, email capture) rather than a file on one machine.

```
MOBILE · NAME · EMAIL · DOB · ANNIVERSARY · GENDER · PREFERRED_STORE
MARKETING_OPT_IN · SOURCE · CREATED_AT · UPDATED_AT
```

**How a save travels** (`store.py` → `sf_profiles.py`):

1. Validate, then commit to local SQLite — instant, and it cannot be lost.
2. Put the mobile in an outbox (`sync_queue`).
3. A background daemon thread pushes it to Snowflake and retries with backoff
   (0s → 15s → 1m → 5m → 15m → 1h) until it lands. The shopper is never made to
   wait on a cold warehouse, and an outage cannot lose their details.
4. The endpoint waits up to 4s so it can honestly say "Saved to your V2 account";
   past that it says "syncing shortly" and the worker finishes the job.

**Snowflake is the source of truth.** If a login finds no local row — new host,
reinstall, or the app moved to a server — the profile is read back from
`CUSTOMER_PROFILE`. The SQLite copy is a cache and an outbox, nothing more.
Any profile row with `synced_at IS NULL` is re-queued automatically on boot.

**Consent is explicit.** `MARKETING_OPT_IN` comes from a checkbox the shopper
ticks; it is `FALSE` unless they do. Honour it before using this table for any
campaign. The CDP contact snapshot only pre-fills the form — it is never pushed
to Snowflake, so `SOURCE = 'CUSTOMER_APP'` always means the shopper confirmed it
themselves.

OTP challenges and sessions stay in SQLite only: ephemeral, high-churn,
point-read on every request, and worthless outside this host.

Writes are confined to that one table. Every other Snowflake call in this app is
read-only — nothing here can write to a sales object.

## The admin panel — `/admin`

A separate password-protected panel for the CRM team. Its own login and session
table: an admin session can never read a customer's bills, and a customer
session can never reach `/admin`. On first boot a random password is printed to
the console once and stored as a PBKDF2 hash in `config.json`; change it under
Settings.

**Offers** — full CRUD over the catalogue. Title, subtitle, badge, icon, the
"how it works" lines, terms, minimum bill, validity window, priority, who sees
it (all / members / non-members / one tier) and where it runs (all stores or a
list of codes). Saving publishes to shoppers immediately.

**Store locations** — see below.

**Nudges** — compose a message, pick an audience, **check the real audience size
before sending**, then send. Audiences: everyone, members, non-members, one
tier, one store's customers, active within N days, lapsed beyond N days, or a
pasted list of mobile numbers. Sent nudges show how many shoppers opened and
dismissed them.

A nudge stores an audience *rule*, not a recipient list — "all customers" is
~46.6M mobiles, and materialising that per nudge would be millions of rows for a
message most never open. The rule is evaluated in Python against the shopper
already loaded at request time, and only the open/dismiss event is written back.

## The store list IS your Excel sheet

**`APP_STORE_LOCATION` is the whole store list.** The app shows these stores and
only these stores — name, city, state, address, phone, hours and coordinates all
come from the sheet you upload in the admin panel. Nothing is read from the
warehouse store master at runtime. A store missing from the sheet does not
appear in Find My Store, and an empty table means an empty store finder (the UI
says so rather than quietly falling back to anything).

Why the warehouse is no longer the source: `RETAIL_HUB.STORE_LOCATION_MASTER`
covers 387 of 439 trading stores, and a dozen of its pins land in the wrong
state. Two found during testing — **HM58 "Gwalior" carries Delhi coordinates**,
and **HK12 "Belgaum-2" points at Mangalore, ~370 km away**. Both would have sent
a customer to the wrong city.

The flow:

1. **Download sheet.** The first download is seeded from the store master so you
   start from a list rather than a blank page. After that it returns *your*
   sheet — it is the working copy. Blank name/city/state/address cells come back
   pre-filled with the warehouse's values as suggestions to confirm or
   overwrite; those suggestions live in the spreadsheet only, never in the app.
2. **Fill it in.** Open the store in Google Maps, right-click the pin, copy the
   coordinates, paste. A single `Lat/Long` column holding `22.1167, 84.0333` —
   or a pasted Maps link — works as well as separate columns.
3. **Upload and preview.** Nothing is written yet: rows read, new stores, new
   pins, what will change, and every skipped row with its reason.
4. **Confirm.** Rows are written to Snowflake and the app shows them at once.

Header matching is forgiving (`Store Code` / `Code` / `ST_CD`, `Latitude` /
`Lat` / `Lat/Long` / `Coordinates`, `City` / `Town`, …) and anything it cannot
map is reported by name rather than silently dropped. Excel's own types are
handled: a time cell becomes `9:30 AM`, and a numeric PIN or phone does not come
through as `770001.0`.

**A blank cell means "leave this as it is"** — uploading a sheet that only fills
addresses will not blank out your coordinates.

**An upload adds and updates; it never silently drops a store.** Stores already
published but absent from the sheet are listed in the preview and left alone.
Taking one down is an explicit **Remove** on its row, or `Show In App = No` to
hide it without deleting.

Rows are rejected, with a reason, for: a duplicate, something that isn't a store
code, a coordinate outside India (6–38 / 68–98), only one of latitude/longitude,
or no store code at all. A code the warehouse has never seen is **accepted** and
merely flagged — a newly opened store must not be blocked because the warehouse
hasn't caught up.

The admin store list also flags **"Check location"** on pins sitting more than
400 km from the middle of their own state. That is a review prompt, not a
verdict: HX52 "Baramati" is filed under Jammu & Kashmir with a pin in
Maharashtra (the *state* is wrong), while HM58's *pin* is wrong. Nothing can
tell which side is at fault from the data alone, so the app never hides or
"corrects" these automatically.

## Installing it as an app (PWA)

`manifest.webmanifest` + `/sw.js` + a generated icon set (`make_icons.py`).
Chrome/Edge/Android offer "Install"; iOS uses Share → Add to Home Screen.

Caching strategy: the app shell is cache-first, API **GET**s are network-first
with the last good response as a fallback (so an installed app still opens your
bills with no signal), and API writes are never cached or queued. Logging out
tells the service worker to drop the cached data — a shared handset must not
leak the previous person's bills.

**The service worker only registers over HTTPS or on localhost.** On a plain
`http://` LAN address the app still works, it just will not install or cache.

## OTP delivery — OneXtel / Aura ✅ LIVE

`app/sms.py` is a full client for the OneXtel Aura API. Configure it in the
admin panel under **OTP SMS** — nothing is hard-coded.

> ### ⚠️ The host is `sapi.onex-aura.com`, NOT `api.onex-aura.com`
>
> ```
> Send    GET  https://sapi.onex-aura.com/api/sms?key=&from=&to=&body=&templateid=&entityid=
> Report  GET  https://sapi.onex-aura.com/api/report?key=&uid=
> ```
>
> The published PDF says `api.onex-aura.com`. On that host **every key on the
> account returns `200 AUTHENTICATION FAILURE`** — which reads as "your
> credentials are wrong" and sends you chasing portal logins, IP whitelists and
> payload shapes. The same key, sender and body on `sapi` returns `100` and
> delivers. **If OTPs ever stop with a `200`, check the host first.**

Note the API answers **HTTP 200 even on failure**; the real status is in the
body (`100` accepted, `200` auth failure, `600` bad sender, `700` bad recipient,
`255` blank template id, `800` blank entity id). `sms.py` reads the body, maps
every code to a sentence a human can act on, and retries only on transport
errors — never on a rejection.

**Acceptance is not delivery.** A `100` only means Aura queued it; the operator
can still drop it for a DLT mismatch, which shows up as `REJECTD` in the
delivery report. Always confirm with **Delivery status** on the send log — a
good send reads `DELIVRD … status 000`.

### Two doc errors that cost a debugging session

1. **`to` must be a string, not a list.** The published doc shows
   `"to": ["xx", "xx"]`. Sending a JSON array makes the API answer
   **`200 AUTHENTICATION FAILURE`** — with a perfectly valid key. The same key
   with `"to": "91…"` returns `700` for a bad number, i.e. auth passed. The
   error blames your credentials when the fault is the payload shape.
2. **`/api/bal` and `/api/summary` reject every key on this account**, working
   ones included — they are not enabled here. A balance call therefore cannot
   test a key. **Check key** instead posts a send to an impossible recipient
   (`910000000000`): `700` means the key is good, and nothing is delivered or
   charged.

### Configuration — all confirmed working

| Field | Value |
|---|---|
| **API key** | `RQmG5mna` (Aura account *V2 Retail Limited*, 2089, type `otp`) |
| **Sender ID** | `VIIRTL` |
| **DLT template ID** | `1007161926972646259` — "Customer App - CRM", Service Implicit |
| **Entity ID** | `1001392810000012986` |
| **Message text** | `Welcome to V2 Retail. Your OTP is : {otp}, Please note that this OTP will expire in 5 min.` |

The message is the registered template character-for-character, with `{#var#}`
replaced by `{otp}`. **Do not reword it** — DLT drops anything that does not
match, and it does so after Aura has accepted the request, so it fails silently.
If the template is ever re-registered, copy the new wording from the Aura portal
(DLT → Template → the ℹ icon in the Content column) and paste it into the admin
panel.

Verified 05-Nov-2026: `100` accepted → delivery report `DELIVRD … status 000`,
customer OTP login end to end, with sending on and developer mode off.

The gateway validates the **recipient first and short-circuits**, so no API call
can confirm the sender ID or the template text. Only a real test send can.
That is why neither is treated as a blocker: blocking on them would prevent the
one thing that can answer the question.

### Finding the sender ID — `find_sender.py`

If you have candidate headers but are not sure which one is registered:

```
python find_sender.py <your-mobile> V2RETL V2SALE V2CLUB V2OFFR
python find_sender.py <your-mobile> V2RETL --save            # keep the winner
```

Those four candidates come from `CDP_Mine\src\pages\Campaigns\SMS.jsx`
(`DLT_HEADERS = ['V2RETL','V2SALE','V2CLUB','V2OFFR']`). The DLT template ids
beside them in that file are clearly fabricated, so treat the headers as a lead,
not a record. **A full sweep of V2's own machines found no confirmed header
anywhere** — the authoritative source is the Aura portal under
*Sender IDs / Headers*, or your DLT entity registration.

This is safe and nearly free. The gateway checks recipient → sender → template,
so a **wrong header is rejected with `600` before the message is submitted** —
nothing is delivered, nothing is charged. The run stops at the first header that
is accepted, so the whole sweep sends at most **one** message: the successful
one. If a candidate is accepted but the SMS still does not arrive, the header is
right and the message text does not match the registered DLT template.

**The message text matters more than it looks.** DLT rejects a body that does
not match the registered template, and it does so *after* Aura has accepted the
request — so it fails silently from our side. The admin screen shows a live
preview with the character count and SMS part count, and warns about the obvious
breakages (no `{otp}`, more than one variable, over 160 characters).

Two independent switches:

- **Send real OTP messages to customers** — off means no customer gets an SMS.
- **Developer mode** — when a send fails, show the OTP on screen instead of
  blocking login. Useful on an internal host; **must be off in production**,
  otherwise anyone can read any customer's OTP off the screen.

With sending on and dev mode off, a failed send returns a clear error and
**clears the OTP challenge**, so the shopper can retry immediately instead of
being held behind the 30-second resend lock waiting for an SMS that never came.

Every send — success or failure — is written to the `sms_log` table and shown
under **Recent sends**, with the Aura tracking id and a "Delivery status" button
that queries the report API. Without that, "the OTP never arrived" is
unanswerable.

**Before going live** also put the app behind a real WSGI server (waitress /
gunicorn) and HTTPS — `run_app.bat` starts Flask's development server.

## Layout

```
Customer App\
  DEPLOY.md                    ← read this to put it on a server
  serve.py                     production entry point (waitress) + preflight
  run_production.bat           production launcher
  run_app.bat                  dev server (Flask), local work only
  requirements.txt             pinned dependencies
  config.example.json          template for config.json (which is gitignored)
  make_icons.py                regenerate the PWA icon set
  seed_offers.py               load the offer catalogue into Snowflake (once)
  find_sender.py               identify which DLT sender ID the gateway accepts
  config.json                  port, dev_mode, admin password hash, use_warehouse_geo
  data\customer_app.db         OTP / sessions / profile outbox  (SQLite, auto-created)
  app\
    server.py                  Flask routes + JSON API + PWA endpoints
    admin.py                   /admin blueprint — auth, offers, locations, nudges
    snowflake_data.py          Snowflake reads (bills/stores/articles), 10-min cache
    sf_profiles.py             Snowflake writes — CUSTOMER_PROFILE
    sf_admin.py                Snowflake writes — offers, locations, nudges
    sms.py                     OneXtel/Aura SMS client — OTP send, balance, DLR
    content.py                 resolves offers/nudges/stores for one shopper
    store_sheet.py             store-location Excel template, parser, preview, commit
    catalog.py                 article-code -> readable product name
    store.py                   SQLite cache + outbox, background sync worker, config
    templates\  index.html  admin.html  offline.html
    static\     styles.css  app.js  admin.css  admin.js  sw.js  icons\
  V2_Retail_Customer_App_Responsive.html   the original static prototype
```

## API

All endpoints except `/`, `/api/health` and the two auth calls need
`Authorization: Bearer <token>`.

| Method | Path | |
|---|---|---|
| POST | `/api/auth/otp` | `{mobile}` → sends OTP (returns `dev_otp` while `dev_mode`) |
| POST | `/api/auth/verify` | `{mobile, otp}` → `{token}` |
| POST | `/api/auth/logout` | drops the session |
| GET | `/api/me` | profile + summary + membership + all bills (`?refresh=1` bypasses cache) |
| GET | `/api/bill/<receipt>` | one bill |
| GET | `/api/bills.csv` | purchase history export |
| POST | `/api/profile` | save name / email / dob / anniversary / preferred store / `marketing_opt_in`; replies `synced: true` once it is in Snowflake |
| GET | `/api/stores` | store list for the picker |
| GET | `/api/offers` | running offers (works signed-out; personalised when signed in) |
| GET | `/api/store-finder` | store directory; `?lat=&lng=` sorts by real distance |
| GET | `/api/nudges` | this shopper's nudges |
| POST | `/api/nudges/<id>/read` | mark read, or `{"dismiss":true}` |
| GET | `/api/health` | Snowflake ping, data-through date, outbox depth, `CUSTOMER_PROFILE` coverage |
| GET | `/manifest.webmanifest`, `/sw.js`, `/offline` | PWA |

Admin (separate bearer token from `/api/admin/login`):

| Method | Path | |
|---|---|---|
| POST | `/api/admin/login` · `/logout` · `/password` | auth |
| GET | `/api/admin/overview` | counts, missing/suspect coordinates, profile coverage |
| GET/POST/DELETE | `/api/admin/offers` | offer CRUD |
| GET | `/api/admin/stores` · `/stores/template` | published list · download the .xlsx |
| POST | `/api/admin/stores/upload` | `?preview=1` parses and reports; without it, commits |
| POST/DELETE | `/api/admin/stores/<code>` | edit one store by hand · remove it from the list |
| GET/POST/DELETE | `/api/admin/nudges` | nudge CRUD; `POST {"send":true}` publishes |
| POST | `/api/admin/nudges/estimate` | real audience size before sending |
| GET/POST | `/api/admin/sms` | SMS gateway settings (the key is only ever returned masked) |
| POST | `/api/admin/sms/verify` | read-only key + balance check; sends nothing |
| POST | `/api/admin/sms/test` | send one real test SMS to a number you type |
| GET | `/api/admin/sms/report/<uid>` | delivery status for one message |

Useful for CRM: `GET /api/health` reports `profile_table` — row count and how
many have a name / email / DOB / marketing opt-in.

## Data rules this app encodes

Worth knowing before changing any number on screen:

1. **`PAYMENTAMOUNT` is the bill total repeated on every line** — take `MAX`, never
   `SUM`. Line value is `PRICE * QUANTITY − DISCOUNT`, and those sum exactly to
   the bill total.
2. **Returns are their own receipt** (store code suffixed `R*`) with a negative
   total and negative quantities. They net off lifetime spend — they are never
   clamped to zero, and they are not counted as purchases in the average bill.
3. **Bills before ~Jul-2024 have NULL `QUANTITY`/`PRICE`/`DISCOUNT`** — only the
   bill total is on record. Those bills show their items and the amount paid, and
   are flagged "not itemised" on the receipt and in the Insights caveat.
4. **The ₹1 carry bag** is rung up and fully discounted on nearly every bill. It
   is shown as "Complimentary" and excluded from savings, otherwise every bill
   would read "saved ₹1".
5. **V2 VIP card tier is derived from card-SKU purchases**
   (`…1730000018` Silver / `…1730000003` Gold / `…1730000004` Platinum,
   2% / 5% / 11%, ₹200 / ₹500 / ₹1,000, valid 1 year). Member discount on a bill
   is inferred with the validated slab-rate rule: non-card line, price ≥ 2,
   `|discount/price − tier_rate| < 0.005`, highest matching tier wins.
6. **Junk POS mobiles are refused at login**
   (`9999999999` and friends — ~6% of network receipts).
7. **Article codes are decoded, not displayed raw.** `catalog.py` turns
   `L_H_CROP_TOP_HS` into "Ladies Crop Top · Half Sleeve". Unknown codes degrade
   to Title Case, never to a blank line.
7b. **Colour and size live at the VARIANT level, not the gen-art level.** Look
   articles up on the full 13-digit code (`ITEMID` with leading zeros stripped:
   gen art + 3-digit suffix), not the 10-digit gen art. `DIM_ARTICLE_V2` holds
   2.6M of its 3.8M rows at variant level; joining on the gen art silently
   returns the parent row where `COLOR` and `SIZE` are blank — which is why the
   first receipts had no "Onion · Size XXL" line.
8. **Offer mechanics are transcribed, not invented.** `seed_offers.py` documents
   the source of every slab and voucher value it loads (the Jul-2026 offer
   catalogue, the Festival Master bill slabs, the live card SKUs). If an offer's
   terms change, change them in the admin panel — do not let the app and the
   counter disagree.

## Performance

One shopper's entire history is a single indexed lookup on `CUSTMOBILENO`
(~2–3 s for 40 bills, ~3 s for 850), plus one article-description query that is
cached process-wide. Bundles are memoised per mobile for 10 minutes. There is no
warm-up or background refresh to run.
