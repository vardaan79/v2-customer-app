"""
admin.py â€” the CRM team's control panel, mounted at /admin.

Manages the three things the shopper app reads but cannot create: offers,
store locations, and nudges. Everything it writes goes to Snowflake
(sf_admin.py), so a second copy of the app â€” or any report â€” sees the same
content. Nothing is kept only in this process.

Auth is deliberately separate from the customer OTP flow: a shared admin
password (hashed, in config.json) and its own session table. An admin session
can never read a customer's bills, and a customer session can never reach /admin.
"""
import os
import json
import secrets
import hashlib
import logging
import datetime as dt

from flask import Blueprint, jsonify, request, render_template

import sms
import store
import sf_admin
import content
import store_sheet
import snowflake_data as data

LOG = logging.getLogger('customer-app.admin')
bp = Blueprint('admin', __name__)

SESSION_HOURS = 12
PBKDF_ROUNDS = 120_000


# ---------------------------------------------------------------- password
def _hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt),
                               PBKDF_ROUNDS).hex()


def ensure_admin_password():
    """Return (password_or_None, was_generated). A fresh install gets a random
    password printed once to the console â€” never a default like 'admin'."""
    cfg = store.config()
    if cfg.get('admin_password_hash') and cfg.get('admin_salt'):
        return None, False
    password = secrets.token_urlsafe(9)
    salt = secrets.token_hex(16)
    cfg['admin_salt'] = salt
    cfg['admin_password_hash'] = _hash(password, salt)
    with open(store.CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2)
    return password, True


def check_password(password):
    cfg = store.config()
    salt, want = cfg.get('admin_salt'), cfg.get('admin_password_hash')
    if not salt or not want:
        return False
    return secrets.compare_digest(_hash(str(password or ''), salt), want)


def set_password(new_password):
    if len(str(new_password or '')) < 8:
        return False, 'Password must be at least 8 characters.'
    cfg = store.config()
    salt = secrets.token_hex(16)
    cfg['admin_salt'] = salt
    cfg['admin_password_hash'] = _hash(str(new_password), salt)
    with open(store.CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2)
    return True, ''


# ---------------------------------------------------------------- sessions
def _init_table():
    with store._lock, store._conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS admin_sessions (
                        token TEXT PRIMARY KEY, created_at TEXT, expires_at TEXT,
                        who TEXT)""")


def _new_session(who='admin'):
    _init_table()
    token = secrets.token_urlsafe(32)
    now = dt.datetime.now()
    with store._lock, store._conn() as c:
        c.execute('INSERT INTO admin_sessions (token, created_at, expires_at, who) '
                  'VALUES (?,?,?,?)',
                  (token, now.isoformat(timespec='seconds'),
                   (now + dt.timedelta(hours=SESSION_HOURS)).isoformat(timespec='seconds'),
                   who))
        c.execute('DELETE FROM admin_sessions WHERE expires_at < ?',
                  (now.isoformat(timespec='seconds'),))
    return token


def current_admin():
    auth = request.headers.get('Authorization', '')
    token = auth[7:].strip() if auth.lower().startswith('bearer ') else ''
    if not token:
        return None
    _init_table()
    with store._lock, store._conn() as c:
        row = c.execute('SELECT who, expires_at FROM admin_sessions WHERE token = ?',
                        (token,)).fetchone()
    if not row or dt.datetime.now() > dt.datetime.fromisoformat(row['expires_at']):
        return None
    return row['who']


def _guard():
    return current_admin()


def fail(msg, code=400):
    return jsonify({'ok': False, 'error': msg}), code


def _require():
    who = _guard()
    return (None, who) if who else (fail('Please sign in to the admin panel.', 401), None)


# ================================================================== routes
@bp.route('/admin')
def admin_page():
    return render_template('admin.html')


@bp.post('/api/admin/login')
def admin_login():
    body = request.json or {}
    if not check_password(body.get('password')):
        LOG.warning('admin login failed from %s', request.remote_addr)
        return fail('Incorrect password.', 401)
    return jsonify({'ok': True, 'token': _new_session(), 'expires_hours': SESSION_HOURS})


@bp.post('/api/admin/logout')
def admin_logout():
    auth = request.headers.get('Authorization', '')
    token = auth[7:].strip() if auth.lower().startswith('bearer ') else ''
    with store._lock, store._conn() as c:
        c.execute('DELETE FROM admin_sessions WHERE token = ?', (token,))
    return jsonify({'ok': True})


@bp.post('/api/admin/password')
def admin_password():
    err, _ = _require()
    if err:
        return err
    body = request.json or {}
    if not check_password(body.get('current')):
        return fail('Current password is incorrect.', 401)
    ok, why = set_password(body.get('new'))
    return jsonify({'ok': True}) if ok else fail(why)


@bp.get('/api/admin/overview')
def overview():
    err, _ = _require()
    if err:
        return err
    stores = content.store_directory(include_hidden=True)
    missing = [s for s in stores if not s['has_geo']]
    suspect = [s for s in stores if s.get('suspect_km')]
    try:
        prof = __import__('sf_profiles').stats()
    except Exception as exc:
        prof = {'error': str(exc)}
    return jsonify({'ok': True, **sf_admin.admin_overview(),
                    'stores_listed': len(stores),
                    'stores_missing_geo': len(missing),
                    'stores_suspect_geo': len(suspect),
                    'missing_geo_codes': [s['code'] for s in missing],
                    'suspect_geo_codes': [s['code'] for s in suspect],
                    'profiles': prof,
                    'data_through': str(data.data_through())})


# ---------------------------------------------------------------- offers
@bp.get('/api/admin/offers')
def get_offers():
    err, _ = _require()
    if err:
        return err
    return jsonify({'ok': True, 'offers': sf_admin.list_offers(),
                    'types': list(sf_admin.OFFER_TYPES)})


@bp.post('/api/admin/offers')
def post_offer():
    err, who = _require()
    if err:
        return err
    body = request.json or {}
    if not (body.get('title') or '').strip():
        return fail('An offer needs a title.')
    try:
        oid = sf_admin.save_offer(body, by=who)
    except Exception as exc:
        LOG.exception('offer save failed')
        return fail(f'Could not save: {exc}', 503)
    content.invalidate('offers')
    return jsonify({'ok': True, 'offer_id': oid})


@bp.delete('/api/admin/offers/<offer_id>')
def del_offer(offer_id):
    err, _ = _require()
    if err:
        return err
    sf_admin.delete_offer(offer_id)
    content.invalidate('offers')
    return jsonify({'ok': True})


# ---------------------------------------------------------------- stores
@bp.get('/api/admin/stores')
def get_stores():
    err, _ = _require()
    if err:
        return err
    stores = content.store_directory(include_hidden=True)
    # purely informational: how many trading stores the sheet has not covered
    try:
        traded = data.sf.operational_stores(90)
        not_listed = sorted(traded - {s['code'] for s in stores})
    except Exception:
        not_listed = []
    return jsonify({'ok': True, 'stores': stores,
                    'not_listed_count': len(not_listed),
                    'not_listed': not_listed[:100]})


@bp.post('/api/admin/stores/<code>')
def post_store(code):
    err, who = _require()
    if err:
        return err
    body = dict(request.json or {})
    # accept a pasted Google Maps URL or "lat, lng" blob instead of two fields
    blob = (body.pop('paste', '') or '').strip()
    if blob:
        latlng = _parse_latlng(blob)
        if not latlng:
            return fail('Could not read a latitude/longitude out of that. Paste '
                        '"28.6139, 77.2090" or a Google Maps link containing @lat,lng.')
        body['lat'], body['lng'] = latlng
    try:
        sf_admin.save_location(code, body, by=who)
    except ValueError as exc:
        return fail(str(exc))
    except Exception as exc:
        LOG.exception('location save failed')
        return fail(f'Could not save: {exc}', 503)
    content.invalidate()
    return jsonify({'ok': True, 'code': code.upper()})


@bp.get('/api/admin/stores/template')
def store_template():
    """Download every store pre-filled with the coordinates we hold, so the
    sheet is a correction list rather than a blank page."""
    err, _ = _require()
    if err:
        return err
    from flask import send_file as _send
    # Once a sheet has been uploaded, the template IS that sheet â€” it comes back
    # as the working copy. Only the very first download is seeded from the
    # warehouse, so the team starts from a list rather than a blank page.
    stores = content.store_directory(include_hidden=True)
    if not stores:
        stores = _seed_rows()
    else:
        _suggest_blanks(stores)
    buf = store_sheet.build_template(stores)
    stamp = dt.date.today().isoformat()
    return _send(buf, as_attachment=True,
                 download_name=f'V2_store_locations_{stamp}.xlsx',
                 mimetype='application/vnd.openxmlformats-officedocument.'
                          'spreadsheetml.sheet')


def _seed_rows():
    """First-run starting list: the trading estate with whatever geo the
    warehouse holds. Used ONLY to pre-fill the first template download â€”
    the app never renders anything from here."""
    names = data.store_map()
    op = data.sf.operational_stores(90)
    base = sf_admin.base_locations()
    out = []
    for code in sorted(op | {c for c in base if c in names}):
        meta = names.get(code, {})
        b = base.get(code, {})
        out.append({'code': code, 'name': meta.get('name') or code,
                    'city': meta.get('city') or '', 'state': meta.get('state') or '',
                    'lat': b.get('lat'), 'lng': b.get('lng'),
                    'address': b.get('address') or '', 'pincode': b.get('pincode') or '',
                    'phone': b.get('phone') or '', 'open_time': '', 'close_time': '',
                    'is_active': True})
    return out


def _suggest_blanks(stores):
    """Fill empty name/city/state/address cells in the DOWNLOADED sheet with the
    warehouse's values, as suggestions to confirm or overwrite.

    This touches the spreadsheet only. The app still renders nothing but what
    has been uploaded â€” a suggestion a shopper never sees until the sheet comes
    back with it. It exists so a half-filled sheet does not come back blanker
    than it needs to be.
    """
    try:
        names = data.store_map()
        base = sf_admin.base_locations()
    except Exception:
        return
    for s in stores:
        meta = names.get(s['code'], {})
        b = base.get(s['code'], {})
        if not s.get('name') or s['name'] == s['code']:
            s['name'] = meta.get('name') or s['code']
        s['city'] = s.get('city') or meta.get('city') or ''
        s['state'] = s.get('state') or meta.get('state') or ''
        s['address'] = s.get('address') or b.get('address') or ''
        s['pincode'] = s.get('pincode') or b.get('pincode') or ''
        s['phone'] = s.get('phone') or b.get('phone') or ''


@bp.post('/api/admin/stores/upload')
def store_upload():
    """Two-step: ?preview=1 parses and reports, no write. Without it, commits."""
    err, who = _require()
    if err:
        return err
    f = request.files.get('file')
    if not f or not f.filename:
        return fail('Choose an .xlsx file to upload.')
    if not f.filename.lower().endswith(('.xlsx', '.xlsm')):
        return fail('Please upload an Excel .xlsx file (not .csv or .xls).')

    raw = f.read()
    if len(raw) > 8 * 1024 * 1024:
        return fail('That file is larger than 8 MB â€” is it the right sheet?')

    try:
        known = set(data.store_map())       # advisory: flags codes, never rejects
    except Exception:
        known = None
    try:
        rows, report = store_sheet.parse(raw, known)
    except ValueError as exc:
        return fail(str(exc))
    except Exception as exc:
        LOG.exception('store sheet parse failed')
        return fail(f'Could not read that sheet: {exc}')

    preview = store_sheet.diff(rows, sf_admin.list_stores())

    if request.args.get('preview') == '1':
        return jsonify({'ok': True, 'preview': True, 'report': report,
                        'changes': preview, 'filename': f.filename})

    if not rows:
        return fail('Nothing in that sheet could be imported â€” '
                    'check the preview for what went wrong.')
    saved, failures = store_sheet.commit(rows, by=who)
    content.invalidate()
    LOG.info('store sheet upload by %s: %d saved, %d failed', who, saved, len(failures))
    return jsonify({'ok': True, 'saved': saved, 'failed': failures,
                    'report': report, 'changes': preview})


# ---------------------------------------------------------------- SMS gateway
def _save_cfg(cfg):
    with open(store.CONFIG_PATH, 'w', encoding='utf-8') as fh:
        json.dump(cfg, fh, indent=2)


@bp.get('/api/admin/sms')
def get_sms():
    err, _ = _require()
    if err:
        return err
    cfg = store.config()
    s = sms.settings(cfg)
    return jsonify({
        'ok': True,
        'settings': {**s, 'key': sms.mask(s['key']), 'key_set': bool(s['key'])},
        'configured': sms.is_configured(cfg),
        'complete': sms.is_complete(cfg),
        'missing': sms.missing_fields(cfg),
        'warnings': sms.check_template(s['otp_template'], s['sender']),
        'preview': sms.render(s['otp_template'], '123456'),
        'dev_mode': bool(cfg.get('dev_mode')),
        'log': store.recent_sms(40),
    })


@bp.post('/api/admin/sms')
def post_sms():
    err, _ = _require()
    if err:
        return err
    body = request.json or {}
    cfg = store.config()
    s = sms.settings(cfg)
    for f in ('sender', 'entity_id', 'template_id', 'otp_template'):
        if f in body:
            s[f] = str(body[f] or '').strip()
    # a blank key means "leave the stored one alone" â€” the UI only ever shows a
    # masked value, so sending it back must not overwrite the real key
    if body.get('key'):
        s['key'] = str(body['key']).strip()
    if 'enabled' in body:
        s['enabled'] = bool(body['enabled'])
    if 'dev_mode' in body:
        cfg['dev_mode'] = bool(body['dev_mode'])
    cfg['sms'] = s
    _save_cfg(cfg)
    LOG.info('SMS settings updated (enabled=%s, sender=%s, template=%s)',
             s['enabled'], s['sender'], s['template_id'])
    return jsonify({'ok': True, 'configured': sms.is_configured(cfg),
                    'complete': sms.is_complete(cfg),
                    'missing': sms.missing_fields(cfg),
                    'warnings': sms.check_template(s['otp_template'], s['sender']),
                    'preview': sms.render(s['otp_template'], '123456')})


@bp.post('/api/admin/sms/verify')
def verify_sms():
    """Read-only credential check against the gateway. Sends nothing."""
    err, _ = _require()
    if err:
        return err
    ok, message = sms.verify_credentials(store.config())
    return jsonify({'ok': True, 'valid': ok, 'message': message})


@bp.post('/api/admin/sms/test')
def test_sms():
    """Send one real OTP-shaped message to a number the admin types."""
    err, who = _require()
    if err:
        return err
    mobile = str((request.json or {}).get('mobile', '')).strip()
    if not data.valid_mobile(mobile):
        return fail('Enter a valid 10-digit Indian mobile number.')
    cfg = store.config()
    if not sms.is_complete(cfg):
        return fail('Finish the SMS settings first â€” missing: '
                    + ', '.join(sms.missing_fields(cfg)))
    try:
        res = sms.send_otp(cfg, mobile, '123456')
    except sms.SmsError as exc:
        store.log_sms(mobile, 'test', False, code=exc.code, detail=str(exc))
        hint = ''
        if exc.code == 600:
            hint = (' — this account does need the DLT header. Put your registered '
                    '6-character sender ID in the Sender ID field and try again.')
        elif exc.code == 200:
            hint = (' — the key is being rejected by the gateway. Every key on this '
                    'account fails the same way from this server, which usually '
                    'means the server IP is not whitelisted on the Aura account. '
                    'Ask OneXtel (support@onextel.com) to whitelist it, or to '
                    'confirm the key is active.')
        elif exc.code == 255:
            hint = ' — add the DLT template ID.'
        elif exc.code == 800:
            hint = ' — add the DLT entity ID.'
        return fail(f'The gateway rejected it: {exc}{hint}', 502)
    store.log_sms(mobile, 'test', True, uid=res.get('uid'), code=res.get('code'),
                  detail=f'test by {who}')
    return jsonify({'ok': True, 'uid': res.get('uid'),
                    'message': (f"Accepted by the gateway (tracking id "
                                f"{res.get('uid') or 'n/a'}); the code is 123456. "
                                f"If it does not arrive in a minute, the operator "
                                f"dropped it — usually a DLT template mismatch.")})


@bp.get('/api/admin/sms/report/<uid>')
def sms_report(uid):
    err, _ = _require()
    if err:
        return err
    try:
        return jsonify({'ok': True, **sms.report(store.config(), uid)})
    except sms.SmsError as exc:
        return fail(str(exc), 502)


@bp.delete('/api/admin/stores/<code>')
def del_store(code):
    """Remove a store from the published list entirely."""
    err, _ = _require()
    if err:
        return err
    sf_admin.delete_location(code)
    content.invalidate()
    return jsonify({'ok': True, 'code': str(code).upper()})


def _parse_latlng(text):
    import re
    m = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', text)            # maps URL
    if not m:
        m = re.search(r'(?:q=|destination=|query=)(-?\d+\.\d+),\s*(-?\d+\.\d+)', text)
    if not m:
        m = re.search(r'(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)', text)  # plain pair
    if not m:
        return None
    return float(m.group(1)), float(m.group(2))


# ---------------------------------------------------------------- customer access
@bp.post('/api/admin/customers/view-as')
def view_as_customer():
    """Support access: open a shopper's account without their OTP.

    Returns a one-time 60-second handoff code, never the session token itself
    (see store.create_handoff). Every use is written to login_log — mobile,
    admin name and IP — the same trail a real sign-in leaves, so there is
    always an answer to "who opened this account and when".
    """
    err, who = _require()
    if err:
        return err
    mobile = str((request.json or {}).get('mobile', '')).strip()
    if not data.valid_mobile(mobile):
        return fail('Enter a valid 10-digit Indian mobile number.')
    try:
        if not data.exists(mobile):
            return fail('No V2 purchases found against this number.', 404)
    except Exception as exc:
        return fail(f'Could not reach our billing system right now ({exc}).', 503)
    code = store.create_handoff(mobile, created_by=who)
    masked = f'{mobile[:2]}XXXXXX{mobile[-2:]}'
    store.log(mobile, 'admin_view_as', f'opened by admin "{who}" from {request.remote_addr}')
    LOG.warning('admin %s opened customer %s without OTP (view-as)', who, masked)
    return jsonify({'ok': True, 'code': code, 'mobile': masked})


@bp.get('/api/admin/customers/recent-access')
def recent_access():
    err, _ = _require()
    if err:
        return err
    return jsonify({'ok': True, 'log': store.recent_admin_access(50)})


# ---------------------------------------------------------------- nudges
@bp.get('/api/admin/nudges')
def get_nudges():
    err, _ = _require()
    if err:
        return err
    nudges = sf_admin.list_nudges()
    stats = sf_admin.nudge_stats()
    for n in nudges:
        n.update(stats.get(n['nudge_id'], {'opened': 0, 'dismissed': 0}))
    return jsonify({'ok': True, 'nudges': nudges,
                    'audiences': list(sf_admin.AUDIENCES)})


@bp.post('/api/admin/nudges/estimate')
def estimate():
    err, _ = _require()
    if err:
        return err
    body = request.json or {}
    try:
        n = sf_admin.estimate_audience(body.get('audience_type'), body.get('audience_value'))
    except Exception as exc:
        return fail(f'Could not size that audience: {exc}', 503)
    return jsonify({'ok': True, 'recipients': n})


@bp.post('/api/admin/nudges')
def post_nudge():
    err, who = _require()
    if err:
        return err
    body = request.json or {}
    if not (body.get('title') or '').strip():
        return fail('A nudge needs a title.')
    if not (body.get('body') or '').strip():
        return fail('A nudge needs a message.')
    send = bool(body.get('send'))
    if send and not body.get('est_recipients'):
        try:
            body['est_recipients'] = sf_admin.estimate_audience(
                body.get('audience_type'), body.get('audience_value'))
        except Exception:
            body['est_recipients'] = 0
    try:
        nid = sf_admin.save_nudge(body, by=who, send=send)
    except Exception as exc:
        LOG.exception('nudge save failed')
        return fail(f'Could not save: {exc}', 503)
    content.invalidate('nudges')
    if send:
        LOG.info('nudge %s sent by %s to audience %s=%s (~%s shoppers)', nid, who,
                 body.get('audience_type'), body.get('audience_value'),
                 body.get('est_recipients'))
    return jsonify({'ok': True, 'nudge_id': nid, 'sent': send})


@bp.delete('/api/admin/nudges/<nudge_id>')
def del_nudge(nudge_id):
    err, _ = _require()
    if err:
        return err
    sf_admin.delete_nudge(nudge_id)
    content.invalidate('nudges')
    return jsonify({'ok': True})
