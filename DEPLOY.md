# Deploying the V2 Retail Customer App

This app sends OTPs to real phones and shows real customers their real bills.
Work through this in order and don't skip the checks — `python serve.py --check`
refuses to start on anything that would embarrass you in front of a customer.

---

## 1. What the server needs

| | |
|---|---|
| **Python** | 3.11 or newer (built on 3.14) |
| **Outbound** | `*.snowflakecomputing.com` (443), `sapi.onex-aura.com` (443) |
| **Inbound** | whatever port you choose — behind a reverse proxy, never direct |
| **Disk** | ~50 MB for the app; the SQLite file grows a few MB a year |
| **RAM** | ~300 MB resident; the article cache is the bulk of it |

No database server to install. Snowflake holds everything shared; SQLite holds
only OTPs, sessions and the profile outbox.

---

## 2. Copy and install

```bat
xcopy /E /I "Customer App" D:\apps\v2-customer-app
cd D:\apps\v2-customer-app
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

**Copy the Snowflake key too.** `sf_helper.py` reads
`C:\Users\anand.prakash\Desktop\rajatverma_rsa.p8` by default. On a server, put
it somewhere sane and set the environment variable:

```bat
setx SNOWFLAKE_PRIVATE_KEY_PATH "D:\secure\rajatverma_rsa.p8"
```

Lock that file down to the service account — it is full read access to the
warehouse. `sf_helper.py` also honours `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`,
`SNOWFLAKE_ROLE` and `SNOWFLAKE_WAREHOUSE`.

---

## 3. Configure

Copy `config.example.json` → `config.json` and fill it in. Nothing in the app is
hard-coded; this file is the whole configuration.

```json
{
  "bind_host": "127.0.0.1",
  "port": 5017,
  "behind_proxy": true,
  "dev_mode": false,
  "purchase_cutoff": "2025-04-01",
  "sms": { "enabled": true, "key": "…", "sender": "VIIRTL", … }
}
```

- **`dev_mode` must be `false`.** With it on, a failed SMS shows the OTP on the
  customer's own screen — which means anyone can sign in as anyone.
- **`bind_host`** should be `127.0.0.1` when a proxy on the same box fronts it.
  Use `0.0.0.0` only if the proxy is on another machine and a firewall limits who
  can reach the port.
- **`behind_proxy`** must be `true` behind nginx/IIS, otherwise every customer in
  India looks like one IP to the OTP rate limiter.
- **`purchase_cutoff`** — purchases before this date are not shown. See §7.

On first boot the app prints a generated admin password **once**. Write it down,
then change it under Settings in `/admin`.

---

## 4. Preflight, then run

```bat
python serve.py --check      REM validates, sends nothing, starts nothing
python serve.py              REM runs under waitress
```

`--check` verifies Snowflake, the SMS configuration, the store list, the cutoff
and the session policy, and **refuses to start** if `dev_mode` is on, the admin
password is unset, SMS is not ready, or Snowflake is unreachable. Override with
`--force` only for a deliberately internal instance.

`run_app.bat` starts Flask's development server — use it for local work only.
`run_production.bat` runs `serve.py`.

---

## 5. Run it as a service

Flask/waitress in a console window dies with the session. Use NSSM:

```bat
nssm install V2CustomerApp "D:\apps\v2-customer-app\.venv\Scripts\python.exe" "D:\apps\v2-customer-app\serve.py"
nssm set V2CustomerApp AppDirectory D:\apps\v2-customer-app
nssm set V2CustomerApp AppStdout D:\apps\v2-customer-app\logs\out.log
nssm set V2CustomerApp AppStderr D:\apps\v2-customer-app\logs\err.log
nssm set V2CustomerApp Start SERVICE_AUTO_START
nssm start V2CustomerApp
```

---

## 6. TLS — not optional

OTPs, bills, names and emails all cross the wire. The service worker also
**refuses to register over plain `http://`** except on localhost, so without TLS
nobody can install the app to their home screen.

nginx:

```nginx
server {
    listen 443 ssl http2;
    server_name myaccount.v2retail.com;
    ssl_certificate     /etc/ssl/v2.crt;
    ssl_certificate_key /etc/ssl/v2.key;

    client_max_body_size 10m;          # store-sheet uploads

    location / {
        proxy_pass         http://127.0.0.1:5017;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;        # a cold Snowflake warehouse is slow
    }
}
server { listen 80; server_name myaccount.v2retail.com; return 301 https://$host$request_uri; }
```

**Restrict `/admin` to the office.** It is password-protected, but there is no
reason for it to be reachable from the public internet:

```nginx
location /admin      { allow 103.29.220.0/24; deny all; proxy_pass http://127.0.0.1:5017; }
location /api/admin  { allow 103.29.220.0/24; deny all; proxy_pass http://127.0.0.1:5017; }
```

---

## 7. The purchase cutoff

`"purchase_cutoff": "2025-04-01"` — bills before this date are invisible
everywhere: history, totals, savings, insights, CSV export and the receipt.

**Know what this costs before you go live.** Of ~46.6M customers, **18.8M have
no bill on or after 01-Apr-2025**. They can still sign in — the cutoff gates what
is *shown*, never who may log in — and they get a plain message saying the app
shows purchases from that date and none were found. Gating login instead would
tell 40% of your customers that V2 has never heard of them.

Change the date and restart; nothing needs rebuilding.

---

## 8. Sessions

A sign-in lasts **60 days and renews on every visit**, so a customer who opens
the app even once a month is never asked for an OTP again. Two months of silence
and they get one OTP. The token lives in the browser's localStorage and is sent
as a bearer header — there are no cookies, so no CSRF surface.

Logging out clears the token *and* tells the service worker to drop its cached
API responses, so a shared handset doesn't leak the previous person's bills.

---

## 9. Before you hand out the URL

- [ ] `python serve.py --check` passes with no BLOCK
- [ ] HTTPS works and `http://` redirects
- [ ] `/admin` is restricted and the password has been changed
- [ ] A test OTP reaches a real phone — check **Delivery status** reads
      `DELIVRD … status 000`, not just that it was accepted
- [ ] The store sheet has been uploaded (Find My Store is empty without it)
- [ ] Offer validity dates are set, and the offers actually running are the ones
      switched on
- [ ] Install to a home screen from a phone and open a bill offline
- [ ] `data\customer_app.db` is in the backup job

---

## 10. Keeping it running

**Back up** `config.json` (secrets) and `data\customer_app.db` (sessions +
profile outbox). Everything else is reproducible from this folder and Snowflake.

**Watch** `GET /api/health` — it reports Snowflake reachability, the data-through
date, outbox depth and `CUSTOMER_PROFILE` coverage. A good probe is
`ok == true` and `data_through` within two days of today.

**Upgrading:** stop the service, copy the new files over, delete
`app\__pycache__`, start it. Python will otherwise keep running the old compiled
module and your change will appear to do nothing — this has bitten us.

**Cost:** each OTP is one SMS credit. The balance endpoint is not enabled on this
Aura account, so watch it in the portal, not the app.
