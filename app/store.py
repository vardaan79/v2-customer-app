"""
store.py â€” local SQLite plus the write-through to Snowflake.

What lives where, and why:

  OTP challenges, sessions   SQLite only. Ephemeral, high-churn, point-read on
                             every request â€” exactly what Snowflake is bad at,
                             and worthless outside this host anyway.
  Profile details            SQLite AND Snowflake. The shopper's own name,
                             email, DOB, anniversary, preferred store and
                             marketing consent are real CRM data, so they go to
                             V2RETAIL.CDP_WORK.CUSTOMER_PROFILE (sf_profiles.py).

The profile path is local-first with an outbox: a save commits to SQLite and
returns immediately, then a background worker pushes it to Snowflake and retries
with backoff until it lands. The shopper is never made to wait on a sleeping
warehouse, and a warehouse outage cannot lose their details. On login, if this
host has no local row, the profile is read back from Snowflake â€” so a shopper
keeps their details if the app is reinstalled or moved to a server.
"""
import os
import re
import json
import time
import sqlite3
import secrets
import hashlib
import logging
import threading
import datetime as dt

LOG = logging.getLogger('customer-app.store')   # not `log` â€” that name is the
                                                # login-audit helper further down

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(APP_DIR), 'data')
DB_PATH = os.path.join(DATA_DIR, 'customer_app.db')
CONTACT_SEED = r'E:\ANTIGRAVITY\CDP_Mine\server\contact_cache.db'

OTP_TTL_SECONDS = 300
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_SECONDS = 30

# A shopper checks a bill every few weeks at most. Re-OTPing them each visit is
# the fastest way to make the app feel like a chore, so a session lasts 60 days
# and SLIDES: every authenticated request pushes the expiry back out to a full
# 60 days (see touch_session). Someone who opens the app monthly never sees an
# OTP again; someone who disappears for two months is asked once.
SESSION_DAYS = 60
SESSION_SLIDE_AFTER_HOURS = 12   # don't rewrite the row on every single request

_lock = threading.Lock()


def _conn():
    os.makedirs(DATA_DIR, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    return c


def init():
    with _lock, _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS otp (
            mobile TEXT PRIMARY KEY, code_hash TEXT NOT NULL,
            issued_at TEXT NOT NULL, expires_at TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY, mobile TEXT NOT NULL,
            created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
            user_agent TEXT);
        CREATE INDEX IF NOT EXISTS idx_sessions_mobile ON sessions(mobile);
        CREATE TABLE IF NOT EXISTS profiles (
            mobile TEXT PRIMARY KEY, name TEXT, email TEXT, dob TEXT,
            anniversary TEXT, gender TEXT, preferred_store TEXT,
            marketing_opt_in INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT, synced_at TEXT);
        CREATE TABLE IF NOT EXISTS sync_queue (
            mobile TEXT PRIMARY KEY, queued_at TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0, next_try TEXT,
            last_error TEXT);
        CREATE TABLE IF NOT EXISTS login_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, mobile TEXT, at TEXT,
            event TEXT, detail TEXT);
        CREATE TABLE IF NOT EXISTS sms_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, mobile TEXT, at TEXT,
            purpose TEXT, ok INTEGER, uid TEXT, code INTEGER, detail TEXT);
        CREATE INDEX IF NOT EXISTS idx_sms_log_at ON sms_log(at);
        """)
        # columns added after the first release â€” older DB files get them here
        cols = {r['name'] for r in c.execute('PRAGMA table_info(profiles)')}
        for col, decl in (('marketing_opt_in', 'INTEGER NOT NULL DEFAULT 0'),
                          ('synced_at', 'TEXT')):
            if col not in cols:
                c.execute(f'ALTER TABLE profiles ADD COLUMN {col} {decl}')
        # anything saved before the Snowflake write-through existed (or left over
        # from a warehouse outage) gets picked up by the worker on this boot
        c.execute("""INSERT INTO sync_queue (mobile, queued_at, attempts, next_try)
                     SELECT mobile, ?, 0, ? FROM profiles WHERE synced_at IS NULL
                     ON CONFLICT(mobile) DO NOTHING""", (_iso(_now()), _iso(_now())))
    start_sync_worker()


def _now():
    return dt.datetime.now()


def _iso(t):
    return t.isoformat(timespec='seconds')


def _hash(mobile, code):
    return hashlib.sha256(f'{mobile}:{code}'.encode()).hexdigest()


def log(mobile, event, detail=''):
    with _lock, _conn() as c:
        c.execute('INSERT INTO login_log (mobile, at, event, detail) VALUES (?,?,?,?)',
                  (mobile, _iso(_now()), event, detail))


def log_sms(mobile, purpose, ok, uid=None, code=None, detail=''):
    """Every OTP send, good or bad. Without this, 'the OTP never arrived' is
    unanswerable — the gateway accepts and the operator drops it later."""
    with _lock, _conn() as c:
        c.execute('INSERT INTO sms_log (mobile, at, purpose, ok, uid, code, detail) '
                  'VALUES (?,?,?,?,?,?,?)',
                  (mobile, _iso(_now()), purpose, 1 if ok else 0, uid, code,
                   str(detail)[:300]))
        c.execute("DELETE FROM sms_log WHERE id < (SELECT MAX(id) - 5000 FROM sms_log)")


def recent_sms(limit=50):
    with _lock, _conn() as c:
        rows = c.execute('SELECT mobile, at, purpose, ok, uid, code, detail '
                         'FROM sms_log ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
        tot = c.execute("SELECT COUNT(*), SUM(ok) FROM sms_log WHERE at > ?",
                        (_iso(_now() - dt.timedelta(days=1)),)).fetchone()
    return {
        'sends': [{'mobile': f'{r["mobile"][:2]}XXXXXX{r["mobile"][-2:]}'
                   if r['mobile'] and len(r['mobile']) == 10 else r['mobile'],
                   'at': r['at'], 'purpose': r['purpose'], 'ok': bool(r['ok']),
                   'uid': r['uid'], 'code': r['code'], 'detail': r['detail']}
                  for r in rows],
        'last_24h': int(tot[0] or 0), 'delivered_24h': int(tot[1] or 0),
    }


# ---------------------------------------------------------------- OTP
def issue_otp(mobile):
    """Returns (code, seconds_to_wait). seconds_to_wait > 0 means 'too soon'."""
    now = _now()
    with _lock, _conn() as c:
        row = c.execute('SELECT issued_at FROM otp WHERE mobile = ?', (mobile,)).fetchone()
        if row:
            age = (now - dt.datetime.fromisoformat(row['issued_at'])).total_seconds()
            if age < OTP_RESEND_SECONDS:
                return None, int(OTP_RESEND_SECONDS - age)
        code = f'{secrets.randbelow(1000000):06d}'
        c.execute("""INSERT INTO otp (mobile, code_hash, issued_at, expires_at, attempts)
                     VALUES (?,?,?,?,0)
                     ON CONFLICT(mobile) DO UPDATE SET
                       code_hash=excluded.code_hash, issued_at=excluded.issued_at,
                       expires_at=excluded.expires_at, attempts=0""",
                  (mobile, _hash(mobile, code), _iso(now),
                   _iso(now + dt.timedelta(seconds=OTP_TTL_SECONDS))))
        return code, 0


def clear_otp(mobile):
    """Drop a challenge we could not deliver, so the 30s resend lock does not
    strand the shopper behind an OTP that was never sent."""
    with _lock, _conn() as c:
        c.execute('DELETE FROM otp WHERE mobile = ?', (mobile,))


def verify_otp(mobile, code):
    """-> (ok, reason)."""
    now = _now()
    with _lock, _conn() as c:
        row = c.execute('SELECT * FROM otp WHERE mobile = ?', (mobile,)).fetchone()
        if not row:
            return False, 'Request an OTP first.'
        if now > dt.datetime.fromisoformat(row['expires_at']):
            c.execute('DELETE FROM otp WHERE mobile = ?', (mobile,))
            return False, 'That OTP has expired. Please request a new one.'
        if row['attempts'] >= OTP_MAX_ATTEMPTS:
            c.execute('DELETE FROM otp WHERE mobile = ?', (mobile,))
            return False, 'Too many incorrect attempts. Please request a new OTP.'
        if not secrets.compare_digest(row['code_hash'], _hash(mobile, str(code).strip())):
            c.execute('UPDATE otp SET attempts = attempts + 1 WHERE mobile = ?', (mobile,))
            left = OTP_MAX_ATTEMPTS - (row['attempts'] + 1)
            return False, f'Incorrect OTP. {left} attempt(s) left.'
        c.execute('DELETE FROM otp WHERE mobile = ?', (mobile,))
        return True, ''


# ---------------------------------------------------------------- sessions
def create_session(mobile, user_agent=''):
    token = secrets.token_urlsafe(32)
    now = _now()
    with _lock, _conn() as c:
        c.execute('INSERT INTO sessions (token, mobile, created_at, expires_at, user_agent) '
                  'VALUES (?,?,?,?,?)',
                  (token, mobile, _iso(now), _iso(now + dt.timedelta(days=SESSION_DAYS)),
                   (user_agent or '')[:200]))
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (_iso(now),))
    return token


def session_mobile(token):
    """Resolve a token to a mobile, renewing the session as a side effect.

    The renewal is what keeps people signed in: each visit resets the clock to a
    full SESSION_DAYS. It is throttled to one write per SESSION_SLIDE_AFTER_HOURS
    so a page that fires six API calls doesn't do six UPDATEs.
    """
    if not token:
        return None
    now = _now()
    with _lock, _conn() as c:
        row = c.execute('SELECT mobile, expires_at FROM sessions WHERE token = ?',
                        (token,)).fetchone()
        if not row:
            return None
        expires = dt.datetime.fromisoformat(row['expires_at'])
        if now > expires:
            c.execute('DELETE FROM sessions WHERE token = ?', (token,))
            return None
        fresh = now + dt.timedelta(days=SESSION_DAYS)
        if (fresh - expires) > dt.timedelta(hours=SESSION_SLIDE_AFTER_HOURS):
            c.execute('UPDATE sessions SET expires_at = ? WHERE token = ?',
                      (_iso(fresh), token))
    return row['mobile']


def session_info(token):
    with _lock, _conn() as c:
        row = c.execute('SELECT mobile, created_at, expires_at FROM sessions '
                        'WHERE token = ?', (token,)).fetchone()
    if not row:
        return None
    expires = dt.datetime.fromisoformat(row['expires_at'])
    return {'mobile': row['mobile'], 'created_at': row['created_at'],
            'expires_at': row['expires_at'],
            'days_left': max((expires - _now()).days, 0)}


def drop_session(token):
    with _lock, _conn() as c:
        c.execute('DELETE FROM sessions WHERE token = ?', (token,))


# ---------------------------------------------------------------- profile
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$')
FIELDS = ('name', 'email', 'dob', 'anniversary', 'gender', 'preferred_store',
          'marketing_opt_in')
BOOL_FIELDS = ('marketing_opt_in',)


def _seed_from_contacts(mobile):
    """One-time name/email seed from the CDP contact snapshot, when present."""
    if not os.path.exists(CONTACT_SEED):
        return {}
    try:
        c = sqlite3.connect(f'file:{CONTACT_SEED}?mode=ro', uri=True, timeout=5)
        row = c.execute('SELECT name, email FROM contacts WHERE mobile = ?',
                        (mobile,)).fetchone()
        c.close()
    except Exception:
        return {}
    if not row:
        return {}
    return {'name': (row[0] or '').strip() or None, 'email': (row[1] or '').strip() or None}


def _row_to_profile(row):
    p = {k: row[k] for k in row.keys()}
    p['marketing_opt_in'] = bool(p.get('marketing_opt_in'))
    return p


def _local_profile(mobile):
    with _lock, _conn() as c:
        row = c.execute('SELECT * FROM profiles WHERE mobile = ?', (mobile,)).fetchone()
    return _row_to_profile(row) if row else None


def get_profile(mobile):
    """Local row if we have one; otherwise pull it back from Snowflake (new host,
    reinstall, or a shopper who filled the form on another deployment), and only
    then fall back to the CDP contact snapshot."""
    local = _local_profile(mobile)
    if local:
        return local

    try:
        import sf_profiles
        remote = sf_profiles.fetch(mobile)
    except Exception as exc:                       # warehouse down â€” not fatal
        LOG.warning('profile read-back from Snowflake failed for %s: %s', mobile, exc)
        remote = None
    if remote:
        _write_local(mobile, {k: remote.get(k) for k in FIELDS},
                     updated_at=remote.get('updated_at'), synced=True)
        return _local_profile(mobile) or remote

    # The CDP contact snapshot only pre-fills the form. It is NOT pushed to
    # Snowflake â€” CUSTOMER_PROFILE holds what the shopper actually confirmed,
    # so its SOURCE stays truthful.
    seed = _seed_from_contacts(mobile)
    if seed:
        _write_local(mobile, {k: v for k, v in seed.items() if k in FIELDS})
        return _local_profile(mobile)
    return {'mobile': mobile, 'updated_at': None, 'synced_at': None,
            **{f: (False if f in BOOL_FIELDS else None) for f in FIELDS}}


def _write_local(mobile, clean, updated_at=None, synced=False):
    cols = ', '.join(clean)
    marks = ', '.join('?' * len(clean))
    upd = ', '.join(f'{k}=excluded.{k}' for k in clean)
    stamp = updated_at or _iso(_now())
    with _lock, _conn() as c:
        c.execute(f"""INSERT INTO profiles (mobile, {cols}, updated_at, synced_at)
                      VALUES (?, {marks}, ?, ?)
                      ON CONFLICT(mobile) DO UPDATE SET
                        {upd}, updated_at=excluded.updated_at, synced_at=excluded.synced_at""",
                  (mobile, *clean.values(), stamp, stamp if synced else None))
    return stamp


def save_profile(mobile, patch):
    """Validate, commit locally, then queue the push to Snowflake."""
    clean, errors = {}, []
    for f in FIELDS:
        if f not in patch:
            continue
        if f in BOOL_FIELDS:
            clean[f] = 1 if patch[f] in (True, 1, '1', 'true', 'True', 'on', 'yes') else 0
            continue
        v = (str(patch[f]).strip() or None) if patch[f] is not None else None
        if f == 'email' and v and not EMAIL_RE.match(v):
            errors.append('Please enter a valid email address.')
            continue
        if f in ('dob', 'anniversary') and v:
            try:
                d = dt.date.fromisoformat(v)
            except ValueError:
                errors.append('Please enter a valid date.')
                continue
            if d > dt.date.today():
                errors.append('That date is in the future.')
                continue
        if f == 'name' and v:
            v = v[:60]
        clean[f] = v
    if errors:
        return None, errors[0]
    if not clean:
        return get_profile(mobile), None

    _write_local(mobile, clean)
    queue_sync(mobile)
    return _local_profile(mobile), None


# ---------------------------------------------------------------- Snowflake sync
SYNC_BACKOFF = (0, 15, 60, 300, 900, 3600)     # seconds, by attempt count


def queue_sync(mobile):
    with _lock, _conn() as c:
        c.execute("""INSERT INTO sync_queue (mobile, queued_at, attempts, next_try)
                     VALUES (?,?,0,?)
                     ON CONFLICT(mobile) DO UPDATE SET
                       queued_at=excluded.queued_at, attempts=0,
                       next_try=excluded.next_try, last_error=NULL""",
                  (mobile, _iso(_now()), _iso(_now())))
    _wake.set()


_inflight = set()
_inflight_lock = threading.Lock()


def _due(claim=True):
    """Queue rows ready to retry. `claim` marks them in-flight so the background
    worker and an inline flush_sync() cannot push the same row twice (harmless
    for the MERGE, but it double-counts attempts and wastes a warehouse call)."""
    now = _iso(_now())
    with _lock, _conn() as c:
        rows = c.execute('SELECT mobile, attempts FROM sync_queue '
                         'WHERE next_try IS NULL OR next_try <= ? '
                         'ORDER BY queued_at LIMIT 25', (now,)).fetchall()
    out = []
    for r in rows:
        if not claim:
            out.append((r['mobile'], r['attempts']))
            continue
        with _inflight_lock:
            if r['mobile'] in _inflight:
                continue
            _inflight.add(r['mobile'])
        out.append((r['mobile'], r['attempts']))
    return out


def _release(mobile):
    with _inflight_lock:
        _inflight.discard(mobile)


def _sync_one(mobile, attempts):
    try:
        return _sync_one_inner(mobile, attempts)
    finally:
        _release(mobile)


def _sync_one_inner(mobile, attempts):
    prof = _local_profile(mobile)
    if not prof:
        with _lock, _conn() as c:
            c.execute('DELETE FROM sync_queue WHERE mobile = ?', (mobile,))
        return True
    try:
        import sf_profiles
        sf_profiles.upsert(mobile, prof)
    except Exception as exc:
        nxt = SYNC_BACKOFF[min(attempts + 1, len(SYNC_BACKOFF) - 1)]
        with _lock, _conn() as c:
            c.execute('UPDATE sync_queue SET attempts = attempts + 1, next_try = ?, '
                      'last_error = ? WHERE mobile = ?',
                      (_iso(_now() + dt.timedelta(seconds=nxt)), str(exc)[:300], mobile))
        LOG.warning('profile sync to Snowflake failed for %s (attempt %d, retry in %ds): %s',
                    mobile, attempts + 1, nxt, exc)
        return False
    with _lock, _conn() as c:
        c.execute('DELETE FROM sync_queue WHERE mobile = ?', (mobile,))
        c.execute('UPDATE profiles SET synced_at = ? WHERE mobile = ?',
                  (_iso(_now()), mobile))
    LOG.info('profile synced to Snowflake: %s', mobile)
    return True


_wake = threading.Event()
_worker = None


def _sync_loop():
    while True:
        try:
            for mobile, attempts in _due():
                _sync_one(mobile, attempts)
        except Exception:
            LOG.exception('sync worker iteration failed')
        _wake.wait(30)
        _wake.clear()


def start_sync_worker():
    """One daemon thread drains the outbox; request threads never block on it."""
    global _worker
    if _worker and _worker.is_alive():
        return
    _worker = threading.Thread(target=_sync_loop, name='sf-profile-sync', daemon=True)
    _worker.start()


def sync_status():
    with _lock, _conn() as c:
        pending = c.execute('SELECT COUNT(*) FROM sync_queue').fetchone()[0]
        err = c.execute('SELECT mobile, attempts, last_error FROM sync_queue '
                        'WHERE last_error IS NOT NULL ORDER BY attempts DESC '
                        'LIMIT 1').fetchone()
        total = c.execute('SELECT COUNT(*) FROM profiles').fetchone()[0]
        synced = c.execute('SELECT COUNT(*) FROM profiles '
                           'WHERE synced_at IS NOT NULL').fetchone()[0]
    return {'profiles_local': total, 'profiles_synced': synced,
            'pending_sync': pending,
            'last_error': (f'{err["mobile"]} after {err["attempts"]} attempt(s): '
                           f'{err["last_error"]}') if err else None}


def flush_sync(timeout=20):
    """Drain the outbox synchronously — used to answer the shopper honestly right
    after a save, and by tests. Returns False if anything is still queued."""
    deadline = time.time() + timeout
    while True:
        for mobile, attempts in _due():
            _sync_one(mobile, attempts)
        # claim=False: a row the background worker is already pushing still counts
        # as pending, and must not be claimed-and-leaked by this probe
        if not _due(claim=False):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.5)


# ---------------------------------------------------------------- config
CONFIG_PATH = os.path.join(os.path.dirname(APP_DIR), 'config.json')
DEFAULT_CONFIG = {
    'port': 5017,
    'host': '127.0.0.1',
    # No SMS gateway is wired up. With dev_mode on, the OTP is returned by the
    # API and printed to the console so the app is usable end to end; turn it
    # off and plug a real sender into sms_send() before any public deployment.
    'dev_mode': True,
    'sms_provider': None,
}


def config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding='utf-8') as f:
                cfg.update(json.load(f))
        except Exception:
            pass
    else:
        try:
            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, indent=2)
        except OSError:
            pass
    return cfg
