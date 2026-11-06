"""
sms.py — OTP delivery over the OneXtel / Aura SMS API.

    Domain      https://sapi.onex-aura.com      <- NOT api.onex-aura.com
    Send        GET  /api/sms?key=&from=&to=&body=&templateid=
    Report      GET  /api/report?key=&uid=
    Balance     GET  /api/bal?key=              (not enabled on this account)

THE HOST IS THE WHOLE STORY. The published PDF says `api.onex-aura.com`; this
account sends on **`sapi.onex-aura.com`**. On the documented host every key on
the account returns `200 AUTHENTICATION FAILURE` — which reads as "your
credentials are wrong" and sent this integration chasing portal logins, IP
whitelists and payload shapes for hours. The same key, same sender, same body
on `sapi` returns `100` and delivers. If OTPs ever stop with a `200`, check the
host before touching anything else.

Response codes (from the Aura API doc):
    100  accepted, a tracking UID is returned       600  blank/invalid sender
    200  authentication failure                     700  blank/invalid recipient
    400  unicode not set                            255  blank template id
    500  blank message                              800  blank entity id
                                                    900  no data present
Note the HTTP status is 200 even when the body says 200 = auth failure, so the
*body* is the thing to read, never the status line.

Other things the published doc gets wrong for this account — do not "tidy" them
back:

1. `to` MUST BE A STRING, NOT A LIST. The doc shows `"to": ["xx", "xx"]`; a JSON
   array answers `200 AUTHENTICATION FAILURE` even with a good key.
2. `/api/bal` and `/api/summary` answer "Authencation failure" for EVERY key on
   this account, valid ones included — not enabled here. A balance call
   therefore cannot test whether a key works.

VALIDATION ORDER — and why nothing short of a real send proves a key works:

    recipient  ->  sender (600)  ->  authentication (200)  ->  template

The recipient is checked FIRST and short-circuits. An impossible recipient
returns `700` *without ever testing the credentials*, which looks exactly like
success. Do not read `700` as "the key is good" — that mistake was made here
once. `verify_credentials()` reports only what it can honestly tell.

What this module deliberately does NOT do: invent the message text. Indian DLT
rules mean the body must match the registered template for `templateid`
character-for-character apart from the variable; a mismatch is rejected by the
operator after Aura has accepted it, so it fails silently from our side. The
template text is therefore config, and `check_template()` flags the obvious ways
it can be wrong before anyone wonders why OTPs stopped arriving.
"""
import re
import json
import time
import logging
import urllib.error
import urllib.parse
import urllib.request

LOG = logging.getLogger('customer-app.sms')

DOMAIN = 'https://sapi.onex-aura.com'
SEND_PATH = '/api/sms'          # GET with query params — the shape that works
TIMEOUT = 20
RETRIES = 2                     # on transport errors only, never on a rejection

CODES = {
    100: 'Accepted for delivery',
    200: 'Authentication failure — check the API key',
    400: 'Unicode message but the unicode flag is not set',
    500: 'Blank message body',
    600: 'Blank or invalid sender ID',
    700: 'Blank or invalid recipient number',
    255: 'Blank template ID',
    800: 'Blank entity ID',
    900: 'No data present',
}
OK = 100

OTP_PLACEHOLDER = '{otp}'
DEFAULT_TEMPLATE = ('Your OTP for V2 Retail is {otp}. It is valid for 5 minutes. '
                    'Please do not share it with anyone.')


class SmsError(Exception):
    """Raised when the gateway rejects a send. `.code` is the Aura code."""

    def __init__(self, message, code=None, raw=None):
        super().__init__(message)
        self.code = code
        self.raw = raw


# ---------------------------------------------------------------- config
def settings(cfg):
    s = dict(cfg.get('sms') or {})
    s.setdefault('provider', 'onextel')
    s.setdefault('key', '')
    s.setdefault('sender', '')
    s.setdefault('entity_id', '')
    s.setdefault('template_id', '')
    s.setdefault('otp_template', DEFAULT_TEMPLATE)
    s.setdefault('enabled', False)
    return s


def is_complete(cfg):
    """Everything needed to send is present — says nothing about whether we
    are allowed to send to customers yet. A test send only needs this."""
    return not missing_fields(cfg)


def is_configured(cfg):
    """Complete AND switched on: the condition for sending a customer an OTP."""
    return bool(settings(cfg)['enabled'] and is_complete(cfg))


def missing_fields(cfg):
    """What genuinely blocks a send.

    The sender ID is NOT in here, deliberately. The gateway validates the
    recipient first and short-circuits, so there is no way to find out from the
    API whether this key needs a `from` — and with Match Template enabled it may
    resolve the header itself. Blocking on an unverifiable field would stop the
    one thing that can answer the question: an actual test send. It is raised as
    a warning instead, and a real `600` from the gateway names it precisely.
    """
    s = settings(cfg)
    out = []
    if not s['key']:
        out.append('API key')
    if OTP_PLACEHOLDER not in (s['otp_template'] or ''):
        out.append(f'an {OTP_PLACEHOLDER} placeholder in the message')
    return out


def mask(secret):
    s = str(secret or '')
    return f'{s[:3]}{"•" * max(len(s) - 6, 3)}{s[-3:]}' if len(s) > 6 else ('•' * len(s))


def render(template, otp):
    return (template or DEFAULT_TEMPLATE).replace(OTP_PLACEHOLDER, str(otp))


def check_template(template, cfg_sender=None):
    """Warnings about a setup DLT is likely to reject. Advisory only — the
    registered template is the authority and we cannot read it."""
    warn = []
    t = template or ''
    if cfg_sender is not None and not cfg_sender:
        warn.append('No sender ID set. Most DLT accounts require the registered '
                    '6-character header; if yours does, the gateway replies with '
                    'code 600 and the send log will say so.')
    if OTP_PLACEHOLDER not in t:
        warn.append(f'No {OTP_PLACEHOLDER} placeholder — the OTP would not appear '
                    'in the message.')
    if t.count(OTP_PLACEHOLDER) > 1:
        warn.append('The OTP placeholder appears more than once; most DLT '
                    'templates register a single variable.')
    body = render(t, '123456')
    if len(body) > 160:
        warn.append(f'The message is {len(body)} characters, so it bills as '
                    'more than one SMS.')
    if not re.search(r'[A-Za-z]', t):
        warn.append('The message has no text apart from the variable.')
    return warn


# ---------------------------------------------------------------- transport
def _post(path, payload, timeout=TIMEOUT):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        DOMAIN + path, data=data,
        headers={'Content-Type': 'application/json',
                 'User-Agent': 'V2-Customer-App/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', 'replace')


def _get(path, params, timeout=TIMEOUT):
    url = f'{DOMAIN}{path}?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'User-Agent': 'V2-Customer-App/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', 'replace')


def _parse(raw):
    """Aura answers with JSON, but not always the same shape. Return
    (code, description, uid, parsed)."""
    try:
        body = json.loads(raw)
    except (ValueError, TypeError):
        m = re.search(r'\b(100|200|255|400|500|600|700|800|900)\b', str(raw))
        code = int(m.group(1)) if m else None
        return code, str(raw)[:300], None, None

    if isinstance(body, list):
        body = body[0] if body else {}
    if not isinstance(body, dict):
        return None, str(body)[:300], None, body

    code = body.get('status', body.get('Status', body.get('code')))
    try:
        code = int(code)
    except (TypeError, ValueError):
        code = None
    desc = (body.get('description') or body.get('Info')
            or body.get('message') or '')
    uid = (body.get('uid') or body.get('UID') or body.get('msgid')
           or body.get('messageid'))
    if uid is None and isinstance(body.get('data'), list) and body['data']:
        first = body['data'][0]
        if isinstance(first, dict):
            uid = first.get('uid') or first.get('msgid')
    return code, str(desc), (str(uid) if uid is not None else None), body


# ---------------------------------------------------------------- operations
def send(cfg, mobile, body, campaign='V2-OTP'):
    """Send one SMS. Returns {'uid', 'code', 'description'} or raises SmsError.

    Deliberately checks completeness, not the `enabled` switch — `enabled`
    governs customer OTPs, while an admin test send must work before it is
    turned on.
    """
    s = settings(cfg)
    gaps = missing_fields(cfg)
    if gaps:
        raise SmsError('SMS is not fully configured — missing: ' + ', '.join(gaps))

    mobile = re.sub(r'\D', '', str(mobile))
    if len(mobile) == 10:
        mobile = '91' + mobile                 # Aura expects the country code
    if not re.fullmatch(r'91[6-9]\d{9}', mobile):
        raise SmsError(f'{mobile} is not a valid Indian mobile number.', code=700)

    # `to` as a STRING — a list here returns a bogus auth failure (see module docstring)
    params = {'key': s['key'], 'to': mobile, 'body': body,
              'campaign_name': campaign}
    if s['sender']:
        params['from'] = s['sender']
    if s['template_id']:
        params['templateid'] = str(s['template_id'])
    if s['entity_id']:
        params['entityid'] = str(s['entity_id'])

    last = None
    for attempt in range(RETRIES + 1):
        try:
            raw = _get(SEND_PATH, params)
            break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            LOG.warning('SMS transport error (attempt %d/%d): %s',
                        attempt + 1, RETRIES + 1, exc)
            if attempt < RETRIES:
                time.sleep(1.5 * (attempt + 1))
    else:
        raise SmsError(f'Could not reach the SMS gateway: {last}')

    code, desc, uid, _ = _parse(raw)
    if code != OK:
        raise SmsError(CODES.get(code, desc or 'The SMS gateway rejected the message'),
                       code=code, raw=raw[:300])
    return {'uid': uid, 'code': code,
            'description': desc or CODES[OK], 'raw': raw[:300]}


def send_otp(cfg, mobile, otp):
    s = settings(cfg)
    return send(cfg, mobile, render(s['otp_template'], otp), campaign='V2-OTP')


def balance(cfg):
    """Credit balance. Not enabled on every Aura account — this one rejects the
    call for all keys, so treat a failure as 'unavailable', not 'bad key'."""
    s = settings(cfg)
    if not s['key']:
        raise SmsError('No API key configured.')
    try:
        raw = _get('/api/bal', {'key': s['key']})
    except Exception as exc:
        raise SmsError(f'Could not reach the SMS gateway: {exc}')
    code, desc, _, body = _parse(raw)
    if code == 200:
        raise SmsError('The balance endpoint is not enabled for this account.',
                       code=200, raw=raw[:300])
    credits = None
    if isinstance(body, dict):
        for k in ('balance', 'Balance', 'credit', 'credits', 'description'):
            if k in body:
                m = re.search(r'-?\d+(?:\.\d+)?', str(body[k]))
                if m:
                    credits = float(m.group())
                    break
    return {'credits': credits, 'description': desc or str(body)[:200], 'raw': raw[:300]}


def report(cfg, uid):
    s = settings(cfg)
    if not s['key']:
        raise SmsError('No API key configured.')
    try:
        raw = _get('/api/report', {'key': s['key'], 'uid': uid})
    except Exception as exc:
        raise SmsError(f'Could not reach the SMS gateway: {exc}')
    code, desc, _, body = _parse(raw)
    if code == 200:
        raise SmsError(CODES[200], code=200, raw=raw[:300])
    return {'uid': uid, 'description': desc, 'body': body, 'raw': raw[:300]}


IMPOSSIBLE_NUMBER = '910000000000'   # right shape, cannot be delivered to


def verify_credentials(cfg):
    """Reachability check that sends nothing. -> (ok, message).

    IMPORTANT: this CANNOT confirm the API key. The gateway validates the
    recipient before the credentials, so the impossible number used here always
    short-circuits to `700` — with a good key *and* a bad one. All this proves
    is that the gateway answers and the payload shape is accepted. Only a real
    test send to a real number exercises authentication.
    """
    s = settings(cfg)
    if not s['key']:
        return False, 'No API key saved yet.'

    params = {'key': s['key'], 'to': IMPOSSIBLE_NUMBER,
              'body': render(s['otp_template'], '123456'),
              'campaign_name': 'V2-key-check'}
    if s['sender']:
        params['from'] = s['sender']
    if s['template_id']:
        params['templateid'] = str(s['template_id'])
    if s['entity_id']:
        params['entityid'] = str(s['entity_id'])

    try:
        raw = _get(SEND_PATH, params)
    except Exception as exc:
        return False, f'Could not reach the SMS gateway: {exc}'

    code, desc, _, _ = _parse(raw)
    if code == 700:
        return True, ('Gateway reachable and the request shape is accepted. '
                      'This does NOT confirm the key — the recipient is checked '
                      'before the credentials. Use "Send test SMS" to your own '
                      'number to actually test authentication.')
    if code == 600:
        return False, f"{CODES[600]} — is '{s['sender']}' registered under DLT?"
    if code == 200:
        return False, (CODES[200] + ' (reached before the recipient check, so '
                       'the key is genuinely being rejected)')
    if code == OK:
        return True, 'Gateway accepted the check message.'
    return False, f'{CODES.get(code, desc or "Unexpected reply")} (code {code})'
