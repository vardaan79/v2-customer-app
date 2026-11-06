"""
content.py — the shopper-facing view of admin-managed content:
the offers they qualify for, the store directory, and their nudges.

Offers and nudges are defined once in the admin panel (sf_admin.py) with a
targeting rule. This module resolves those rules against the shopper who is
actually logged in, using the bundle already loaded for them — no extra
Snowflake round trip per rule.
"""
import time
import threading
import datetime as dt
from urllib.parse import quote_plus

import sf_admin

CACHE_TTL = 300          # offers + nudges move rarely; stores move less
STORE_TTL = 900

_lock = threading.Lock()
_cache = {}


def _cached(key, ttl, build):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = build()
    with _lock:
        _cache[key] = (now, value)
    return value


def invalidate(*keys):
    """Called by the admin panel after a save so shoppers see it immediately."""
    with _lock:
        for k in (keys or list(_cache)):
            _cache.pop(k, None)


# ================================================================== stores
def maps_links(lat, lng, label):
    if lat is None or lng is None:
        q = quote_plus(f'V2 Retail {label}')
        return {'maps': f'https://www.google.com/maps/search/?api=1&query={q}',
                'directions': f'https://www.google.com/maps/dir/?api=1&destination={q}',
                'precise': False}
    pin = f'{lat},{lng}'
    return {'maps': f'https://www.google.com/maps/search/?api=1&query={pin}',
            'directions': f'https://www.google.com/maps/dir/?api=1&destination={pin}',
            'precise': True}


def _flag_suspect(rows):
    """Mark stores whose pin sits far from the middle of their own state.

    A hit means the pin OR the state is wrong and nobody can tell which from the
    data alone — HX52 'Baramati' is filed under Jammu & Kashmir with a pin in
    Maharashtra (the state is wrong); HM58 'Gwalior' carries Delhi coordinates
    (the pin is wrong). So this only raises a review flag for the admin panel;
    it never hides the store or suppresses its directions link, because guessing
    would be worse than showing what the warehouse holds.
    """
    from statistics import median
    by_state = {}
    for s in rows:
        if s['has_geo'] and s['state']:
            by_state.setdefault(s['state'], []).append(s)
    centres = {st: (median([x['lat'] for x in v]), median([x['lng'] for x in v]))
               for st, v in by_state.items() if len(v) >= 4}
    for s in rows:
        s['suspect_km'] = None
        c = centres.get(s['state']) if s['has_geo'] else None
        if c:
            km = haversine_km(s['lat'], s['lng'], c[0], c[1])
            if km > 400:
                s['suspect_km'] = km
    return rows


def store_directory(include_hidden=False):
    """The store list, entirely from the sheet uploaded in the admin panel.

    Nothing here is read from the warehouse store master. That was a deliberate
    change: RETAIL_HUB covered only 387 of 439 trading stores and carried pins in
    the wrong state (HM58 'Gwalior' on Delhi, HK12 'Belgaum-2' on Mangalore), so
    the CRM team owns this list in Excel instead. An empty table means an empty
    store finder — by design, and the UI says so rather than quietly falling back.
    """
    def build():
        out = []
        for code, s in sorted(sf_admin.list_stores().items()):
            if not include_hidden and not s.get('is_active', True):
                continue
            lat, lng = s.get('lat'), s.get('lng')
            label = ', '.join(x for x in (s.get('name') or code, s.get('state')) if x)
            out.append({
                'code': code,
                'name': s.get('name') or code,
                'city': s.get('city') or '',
                'state': s.get('state') or '',
                'address': s.get('address') or '',
                'pincode': s.get('pincode') or '',
                'phone': s.get('phone') or '',
                'open_time': s.get('open_time') or '',
                'close_time': s.get('close_time') or '',
                'lat': lat, 'lng': lng,
                'has_geo': lat is not None and lng is not None,
                'is_active': bool(s.get('is_active', True)),
                'source': 'sheet',
                **maps_links(lat, lng, label),
            })
        return _flag_suspect(out)
    return _cached(f'stores:{include_hidden}', STORE_TTL, build)


def haversine_km(lat1, lng1, lat2, lng2):
    from math import radians, sin, cos, asin, sqrt
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    a = (sin(dlat / 2) ** 2
         + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2)
    return round(2 * 6371 * asin(sqrt(a)), 1)


def nearest_stores(lat, lng, limit=10):
    out = []
    for s in store_directory():
        if not s['has_geo']:
            continue
        out.append({**s, 'distance_km': haversine_km(lat, lng, s['lat'], s['lng'])})
    out.sort(key=lambda s: s['distance_km'])
    return out[:limit]


# ================================================================== offers
def active_offers():
    return _cached('offers', CACHE_TTL, lambda: sf_admin.list_offers(active_only=True))


def _segment_ok(segment, membership):
    seg = (segment or 'ALL').upper()
    if seg == 'ALL':
        return True
    if seg == 'MEMBER':
        return bool(membership.get('is_member'))
    if seg == 'NON_MEMBER':
        return not membership.get('is_member')
    if seg in ('SILVER', 'GOLD', 'PLATINUM'):
        return (membership.get('tier') or '').upper() == seg
    return True


def offers_for(bundle, store_code=None):
    """Offers this shopper can actually use, most relevant first.

    `relevant` marks an offer tied to a store they shop at — shown first rather
    than hidden, because a shopper may well travel to another V2 store.
    """
    membership = bundle.get('membership', {}) if bundle else {}
    my_stores = {s['code'] for s in bundle['summary']['by_store']} if bundle else set()
    if store_code:
        my_stores = {store_code.upper()}

    out = []
    for o in active_offers():
        if not _segment_ok(o.get('segment'), membership):
            continue
        scope_stores = set(o.get('stores') or [])
        if (o.get('scope') or 'ALL').upper() == 'STORES':
            if not scope_stores:
                continue                      # store-scoped but nothing mapped
            relevant = bool(scope_stores & my_stores)
        else:
            relevant = True
        out.append({**o, 'relevant': relevant,
                    'store_count': len(scope_stores) if scope_stores else None})
    out.sort(key=lambda o: (not o['relevant'], o.get('priority', 100), o.get('title') or ''))
    return out


# ================================================================== nudges
def _audience_match(nudge, mobile, bundle):
    a = (nudge.get('audience_type') or 'ALL').upper()
    v = str(nudge.get('audience_value') or '').strip()
    summary = (bundle or {}).get('summary', {})
    membership = (bundle or {}).get('membership', {})

    if a == 'ALL':
        return True
    if a == 'MEMBER':
        return bool(membership.get('is_member'))
    if a == 'NON_MEMBER':
        return not membership.get('is_member')
    if a == 'TIER':
        return (membership.get('tier') or '').upper() == v.upper()
    if a == 'STORE':
        return v.upper() in {s['code'] for s in summary.get('by_store', [])}
    if a == 'ACTIVE':
        d = summary.get('days_since_last')
        return d is not None and d <= int(v or 90)
    if a == 'LAPSED':
        d = summary.get('days_since_last')
        return d is not None and d > int(v or 180)
    if a == 'MOBILES':
        return str(mobile) in {m.strip() for m in v.replace('\n', ',').split(',')}
    return False


def nudges_for(mobile, bundle):
    sent = _cached('nudges', CACHE_TTL, lambda: sf_admin.list_nudges(sent_only=True))
    try:
        state = sf_admin.nudge_state_for(mobile)
    except Exception:
        state = {}
    out = []
    for n in sent:
        if not _audience_match(n, mobile, bundle):
            continue
        st = state.get(n['nudge_id'], {})
        if st.get('dismissed'):
            continue
        out.append({'nudge_id': n['nudge_id'], 'title': n['title'], 'body': n['body'],
                    'cta_label': n['cta_label'], 'cta_tab': n['cta_tab'],
                    'offer_id': n['offer_id'], 'sent_at': n['sent_at'],
                    'read': bool(st.get('read'))})
    return out
