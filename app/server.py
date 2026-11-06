"""
server.py — V2 Retail Customer App (CRM-facing shopper portal).

    run:   run_app.bat      ->  http://127.0.0.1:5017

Flask serves one page plus a small JSON API. Bills, stores, articles and the
V2 VIP card all come live from Snowflake (snowflake_data.py); OTP, sessions and
profile edits live in a local SQLite file (store.py).

Auth: mobile -> OTP -> bearer token. The mobile must have at least one V2 bill
in Snowflake, otherwise there is nothing to show and login is refused.
"""
import io
import os
import csv
import sys
import time
import logging
import datetime as dt

from flask import Flask, jsonify, request, render_template, Response, send_file

sys.path.insert(0, r'E:\ANAND PRAKASH\AUTOMATION')

import sms                        # noqa: E402
import store                      # noqa: E402
import sf_profiles                # noqa: E402
import sf_admin                   # noqa: E402
import content                    # noqa: E402
import admin as admin_mod         # noqa: E402
import snowflake_data as data     # noqa: E402

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024   # store-sheet uploads
app.register_blueprint(admin_mod.bp)
CFG = store.config()
log = logging.getLogger('customer-app')

# Behind nginx/IIS the remote address is the proxy's. Without this, the OTP rate
# limit and the admin login log would see one IP for every customer in India.
if CFG.get('behind_proxy', True):
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


@app.after_request
def _security_headers(resp):
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    resp.headers.setdefault('X-Frame-Options', 'DENY')
    resp.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    resp.headers.setdefault('Permissions-Policy',
                            'geolocation=(self), camera=(), microphone=()')
    if request.path.startswith('/api/'):
        resp.headers.setdefault('Cache-Control', 'no-store')
    return resp


# ================================================================== helpers
def fail(message, code=400, **extra):
    return jsonify({'ok': False, 'error': message, **extra}), code


def current_mobile():
    auth = request.headers.get('Authorization', '')
    token = auth[7:].strip() if auth.lower().startswith('bearer ') else ''
    return store.session_mobile(token), token


def require_login():
    mobile, _ = current_mobile()
    return mobile


def mask(mobile):
    m = str(mobile or '')
    return f'{m[:2]}XXXXXX{m[-2:]}' if len(m) == 10 else m


def sms_send(mobile, code):
    """Deliver the OTP over the OneXtel/Aura gateway.

    -> (delivered, error). A send is attempted whenever SMS is configured and
    enabled; if it fails we say so rather than pretending it went. dev_mode is
    the fallback for a host with no gateway — it prints the OTP instead.
    """
    cfg = store.config()
    if sms.is_configured(cfg):
        try:
            res = sms.send_otp(cfg, mobile, code)
            store.log_sms(mobile, 'otp', True, uid=res.get('uid'),
                          code=res.get('code'), detail=res.get('description'))
            log.info('OTP sent to %s (uid %s)', mask(mobile), res.get('uid'))
            return True, None
        except sms.SmsError as exc:
            store.log_sms(mobile, 'otp', False, code=exc.code, detail=str(exc))
            log.error('OTP send to %s failed: %s', mask(mobile), exc)
            if not cfg.get('dev_mode'):
                return False, str(exc)
            log.warning('dev_mode is on — falling back to printing the OTP')

    if cfg.get('dev_mode'):
        log.warning('OTP for %s: %s  (dev_mode — no SMS sent)', mask(mobile), code)
        print(f'[OTP] {mask(mobile)} -> {code}', flush=True)
        return False, None

    gaps = sms.missing_fields(cfg)
    reason = ('SMS is not switched on in the admin panel'
              if not sms.settings(cfg)['enabled']
              else 'SMS is not fully configured — missing: ' + ', '.join(gaps))
    store.log_sms(mobile, 'otp', False, detail=reason)
    return False, reason


def greeting_name(profile, mobile):
    name = (profile or {}).get('name')
    return name.split()[0].title() if name else f'+91 {mask(mobile)}'


def initials(profile, mobile):
    name = (profile or {}).get('name')
    if name:
        bits = [b for b in name.split() if b]
        return (bits[0][0] + (bits[-1][0] if len(bits) > 1 else '')).upper()
    return str(mobile)[:2]


# ================================================================== page
@app.route('/')
def index():
    return render_template('index.html', year=dt.date.today().year)


# ================================================================== PWA
@app.route('/manifest.webmanifest')
def manifest():
    return jsonify({
        'name': 'V2 Retail — My Account',
        'short_name': 'V2 Retail',
        'description': 'Your V2 bills, savings, offers and VIP membership.',
        'start_url': '/?src=pwa',
        'scope': '/',
        'display': 'standalone',
        'orientation': 'portrait',
        'background_color': '#202020',
        'theme_color': '#202020',
        'lang': 'en-IN',
        'categories': ['shopping', 'lifestyle'],
        'icons': [
            {'src': '/static/icons/icon-192.png', 'sizes': '192x192',
             'type': 'image/png', 'purpose': 'any'},
            {'src': '/static/icons/icon-512.png', 'sizes': '512x512',
             'type': 'image/png', 'purpose': 'any'},
            {'src': '/static/icons/maskable-512.png', 'sizes': '512x512',
             'type': 'image/png', 'purpose': 'maskable'},
        ],
        'shortcuts': [
            {'name': 'My bills', 'url': '/?tab=purchases'},
            {'name': 'Offers', 'url': '/?tab=offers'},
            {'name': 'Find a store', 'url': '/?tab=stores'},
        ],
    })


@app.route('/sw.js')
def service_worker():
    """Served from the root so its scope covers the whole app — a service worker
    under /static/ could only control /static/."""
    path = os.path.join(app.static_folder, 'sw.js')
    return send_file(path, mimetype='application/javascript',
                     max_age=0, conditional=False)


@app.route('/offline')
def offline():
    return render_template('offline.html')


@app.route('/api/health')
def health():
    try:
        body = {'ok': True, **data.health(), 'dev_mode': CFG['dev_mode'],
                'profile_sync': store.sync_status()}
        try:
            body['profile_table'] = sf_profiles.stats()
        except Exception as exc:
            body['profile_table'] = {'error': str(exc)}
        return jsonify(body)
    except Exception as exc:
        return fail(f'Snowflake unreachable: {exc}', 503)


# ================================================================== auth
@app.post('/api/auth/otp')
def send_otp():
    mobile = str((request.json or {}).get('mobile', '')).strip()
    if not data.valid_mobile(mobile):
        return fail('Please enter a valid 10-digit Indian mobile number.')
    try:
        if not data.exists(mobile):
            store.log(mobile, 'otp_denied', 'no bills')
            return fail('We could not find any V2 purchases against this number. '
                        'Please use the mobile number you share at the billing counter.', 404)
    except Exception as exc:
        log.exception('lookup failed')
        return fail(f'Could not reach our billing system right now ({exc}).', 503)

    code, wait = store.issue_otp(mobile)
    if code is None:
        return fail(f'An OTP was just sent. Please wait {wait}s before asking again.', 429,
                    retry_after=wait)
    delivered, err = sms_send(mobile, code)
    cfg = store.config()
    if err and not cfg.get('dev_mode'):
        # the shopper cannot possibly continue — say so instead of leaving them
        # staring at an OTP box that will never fill
        store.log(mobile, 'otp_failed', err)
        store.clear_otp(mobile)
        return fail('We could not send your OTP right now. Please try again in a '
                    'few minutes.', 503)
    store.log(mobile, 'otp_sent', 'sms' if delivered else 'dev')
    body = {'ok': True, 'masked': mask(mobile), 'delivered': delivered,
            'expires_in': store.OTP_TTL_SECONDS}
    if cfg.get('dev_mode') and not delivered:
        body['dev_otp'] = code          # no SMS gateway reached on this host
    return jsonify(body)


@app.post('/api/auth/verify')
def verify():
    payload = request.json or {}
    mobile = str(payload.get('mobile', '')).strip()
    code = str(payload.get('otp', '')).strip()
    if not data.valid_mobile(mobile):
        return fail('Please enter a valid 10-digit Indian mobile number.')
    ok, why = store.verify_otp(mobile, code)
    if not ok:
        store.log(mobile, 'otp_failed', why)
        return fail(why, 401)
    token = store.create_session(mobile, request.headers.get('User-Agent', ''))
    store.log(mobile, 'login')
    return jsonify({'ok': True, 'token': token, 'mobile': mask(mobile)})


@app.post('/api/auth/logout')
def logout():
    _, token = current_mobile()
    store.drop_session(token)
    return jsonify({'ok': True})


# ================================================================== data
@app.get('/api/me')
def me():
    mobile = require_login()
    if not mobile:
        return fail('Please sign in again.', 401)
    t0 = time.time()
    try:
        bundle = data.bundle(mobile, force=request.args.get('refresh') == '1')
    except Exception as exc:
        log.exception('bundle failed')
        return fail(f'Could not load your purchases right now ({exc}).', 503)
    profile = store.get_profile(mobile)
    return jsonify({
        'ok': True,
        'mobile': mask(mobile),
        'profile': profile,
        'greeting': greeting_name(profile, mobile),
        'initials': initials(profile, mobile),
        'summary': bundle['summary'],
        'membership': bundle['membership'],
        'bills': bundle['bills'],
        'data_through': bundle['data_through'],
        'cutoff': bundle['cutoff'],
        'fetched_at': bundle['fetched_at'],
        'session': store.session_info(current_mobile()[1]),
        'took_ms': int((time.time() - t0) * 1000),
    })


@app.get('/api/bill/<path:receipt>')
def bill(receipt):
    mobile = require_login()
    if not mobile:
        return fail('Please sign in again.', 401)
    bundle = data.bundle(mobile)
    for b in bundle['bills']:
        if b['receipt'] == receipt:
            return jsonify({'ok': True, 'bill': b, 'membership': bundle['membership'],
                            'profile': store.get_profile(mobile), 'mobile': mask(mobile)})
    return fail('Bill not found against your number.', 404)


@app.get('/api/bills.csv')
def bills_csv():
    mobile = require_login()
    if not mobile:
        return fail('Please sign in again.', 401)
    bundle = data.bundle(mobile)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['Bill number', 'Type', 'Date', 'Time', 'Store', 'City', 'State', 'Items',
                'MRP value', 'Discount', 'Amount', 'Itemised'])
    for b in bundle['bills']:
        w.writerow([b['receipt'], 'Return' if b['is_return'] else 'Purchase',
                    b['date_iso'], b['time_label'], b['store_name'],
                    b['city'], b['state'], b['items'],
                    f"{b['gross']:.2f}", f"{b['discount']:.2f}", f"{b['total']:.2f}",
                    'Y' if b['itemised'] else 'N'])
    stamp = dt.date.today().isoformat()
    return Response(buf.getvalue(), mimetype='text/csv', headers={
        'Content-Disposition': f'attachment; filename=V2_purchases_{mask(mobile)}_{stamp}.csv'})


@app.post('/api/profile')
def profile_save():
    mobile = require_login()
    if not mobile:
        return fail('Please sign in again.', 401)
    profile, err = store.save_profile(mobile, request.json or {})
    if err:
        return fail(err)
    # The save is already committed locally and queued for Snowflake. Give the
    # worker a moment so the usual case reports "saved to V2's records" honestly,
    # but never hold the shopper up if the warehouse is cold.
    store.flush_sync(timeout=4)
    profile = store.get_profile(mobile)
    return jsonify({'ok': True, 'profile': profile,
                    'synced': bool(profile.get('synced_at')),
                    'greeting': greeting_name(profile, mobile),
                    'initials': initials(profile, mobile)})


@app.get('/api/stores')
def stores():
    """Store list for the 'preferred store' picker — the same uploaded sheet the
    store finder uses, so a shopper can never pick a store the app doesn't show."""
    out = sorted(({'code': s['code'], 'name': s['name'], 'city': s['city'],
                   'state': s['state']} for s in content.store_directory()),
                 key=lambda s: (s['state'] or '', s['name'] or ''))
    return jsonify({'ok': True, 'stores': out})


# ================================================================== offers
@app.get('/api/offers')
def offers():
    """Running offers. Open to a signed-out visitor too — offers are public;
    a signed-in shopper additionally gets them ordered by relevance to them."""
    mobile = require_login()
    bundle = None
    if mobile:
        try:
            bundle = data.bundle(mobile)
        except Exception:
            bundle = None
    try:
        items = content.offers_for(bundle)
    except Exception as exc:
        log.exception('offers failed')
        return fail(f'Could not load offers right now ({exc}).', 503)
    return jsonify({'ok': True, 'offers': items, 'personalised': bundle is not None})


# ================================================================== find a store
@app.get('/api/store-finder')
def store_finder():
    """Full store directory. `lat`/`lng` sorts by distance from the shopper."""
    try:
        lat = request.args.get('lat', type=float)
        lng = request.args.get('lng', type=float)
        if lat is not None and lng is not None:
            items = content.nearest_stores(lat, lng, limit=int(request.args.get('limit', 200)))
            return jsonify({'ok': True, 'stores': items, 'sorted_by': 'distance'})
        items = content.store_directory()
    except Exception as exc:
        log.exception('store finder failed')
        return fail(f'Could not load stores right now ({exc}).', 503)
    items.sort(key=lambda s: (s['state'] or '', s['city'] or '', s['name'] or ''))
    return jsonify({'ok': True, 'stores': items, 'sorted_by': 'name',
                    'with_directions': sum(1 for s in items if s['has_geo'])})


@app.get('/api/store-count')
def store_count():
    """Lets the UI tell "no stores published yet" apart from "search found none"."""
    try:
        return jsonify({'ok': True, 'published': len(content.store_directory())})
    except Exception as exc:
        return fail(str(exc), 503)


# ================================================================== nudges
@app.get('/api/nudges')
def nudges():
    mobile = require_login()
    if not mobile:
        return fail('Please sign in again.', 401)
    try:
        bundle = data.bundle(mobile)
        items = content.nudges_for(mobile, bundle)
    except Exception as exc:
        log.warning('nudges unavailable: %s', exc)
        items = []
    return jsonify({'ok': True, 'nudges': items,
                    'unread': sum(1 for n in items if not n['read'])})


@app.post('/api/nudges/<nudge_id>/read')
def nudge_read(nudge_id):
    mobile = require_login()
    if not mobile:
        return fail('Please sign in again.', 401)
    dismissed = bool((request.json or {}).get('dismiss'))
    try:
        sf_admin.mark_nudge_read(nudge_id, mobile, dismissed=dismissed)
    except Exception as exc:
        return fail(f'Could not update that ({exc}).', 503)
    return jsonify({'ok': True})


# ================================================================== main
def main():
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    store.init()
    host, port = CFG.get('host', '127.0.0.1'), int(CFG.get('port', 5017))
    print('=' * 68)
    print(' V2 Retail — Customer App')
    print(f' http://{host}:{port}')
    try:
        h = data.health()
        print(f" Snowflake OK · data through {h['data_through']} · {h['stores']} stores")
    except Exception as exc:
        print(f' Snowflake NOT reachable: {exc}')
    try:
        sf_profiles.ensure_table()
        s = sf_profiles.stats()
        print(f" Profiles -> {sf_profiles.TABLE} ({s['profiles']} rows, "
              f"{s['opted_in']} opted in)")
    except Exception as exc:
        print(f' Profile table NOT ready: {exc}')
    pending = store.sync_status()['pending_sync']
    if pending:
        print(f' {pending} profile(s) waiting to sync to Snowflake')
    try:
        sf_admin.ensure_tables()
        ov = sf_admin.admin_overview()
        print(f" Admin panel  http://{host}:{port}/admin  "
              f"({ov['offers_active']} live offers, {ov['nudges_sent']} nudges sent)")
    except Exception as exc:
        print(f' Admin tables NOT ready: {exc}')
    pwd, generated = admin_mod.ensure_admin_password()
    if generated:
        print('')
        print(' ' + '*' * 64)
        print(f'  ADMIN PASSWORD (shown once): {pwd}')
        print('  Saved as a hash in config.json. Change it under Settings.')
        print(' ' + '*' * 64)
    if CFG['dev_mode']:
        print(' dev_mode ON — OTPs are printed here and returned by the API')
    print('=' * 68)
    # threaded: a slow Snowflake call must not block every other request
    app.run(host=host, port=port, threaded=True, debug=False)


if __name__ == '__main__':
    main()
