"""
snowflake_data.py — every number the customer app shows comes from here.

Source of truth is Snowflake (standing rule, see the Snowflake migration note):
  V2RETAIL.CDP_WORK.CRM_SALES_LIVE_UC   bill lines (mirror history + same-day Axapta slice)
  V2RETAIL.GOLD.DIM_STORE_V2            store names
  V2RETAIL.RETAIL_HUB.STORES            store gap-fill (DIM lags new openings)
  V2RETAIL.SILVER.DIM_ARTICLE_V2        article description / material group
  V2RETAIL.CDP_WORK.TBL_ARTICLE         major category + division
  V2RETAIL.CDP_WORK.ARTMASTER_EAN       product label fallback

Freshness: the firm T-1 mirror has been lagging, so this reads the LIVE stitched
view. History is byte-identical to the mirror; the most recent day or two is
indicative (~3%) until the mirror catches up. The UI states the data-through date.

A shopper's whole purchase history is one indexed lookup on CUSTMOBILENO
(~2-3s for a typical basket count), so there is no pre-load or warm cache to
maintain — results are memoised per mobile for CACHE_TTL and that is enough.
"""
import os
import sys
import time
import threading
import datetime as dt

sys.path.insert(0, r'E:\ANAND PRAKASH\AUTOMATION')
import sf_helper as sf  # noqa: E402

import catalog  # noqa: E402

# ---------------------------------------------------------------- V2 VIP card
# Card SKUs and slab rates, as validated in the membership-card report family.
CARD_SKUS = {
    '000000001730000018': 'Silver',
    '000000001730000003': 'Gold',
    '000000001730000004': 'Platinum',
}
TIER_RANK = {'Silver': 1, 'Gold': 2, 'Platinum': 3}
TIER_RATE = {'Silver': 0.02, 'Gold': 0.05, 'Platinum': 0.11}
TIER_FEE = {'Silver': 200, 'Gold': 500, 'Platinum': 1000}
RATE_TOL = 0.005
CARD_VALIDITY_DAYS = 365
NEXT_TIER = {'Silver': 'Gold', 'Gold': 'Platinum', 'Platinum': None}

CACHE_TTL = 600          # per-customer bundle, seconds
MASTER_TTL = 86400       # store master, seconds

# ---------------------------------------------------------------- visibility
# Only purchases on or after this date are shown. Set in config.json as
# "purchase_cutoff": "YYYY-MM-DD".
#
# The cutoff gates WHAT IS SHOWN, never who may sign in. 18.8M of the 46.6M
# customers have no bill after 01-Apr-2025; gating login on the cutoff would
# lock 40% of the base out of their own account with a "we can't find you"
# message, which is both wrong and alarming. They sign in and get an empty
# state that explains the window instead.
DEFAULT_CUTOFF = dt.date(2025, 4, 1)


def purchase_cutoff():
    import store as _store
    raw = _store.config().get('purchase_cutoff')
    if not raw:
        return DEFAULT_CUTOFF
    try:
        return dt.date.fromisoformat(str(raw))
    except ValueError:
        return DEFAULT_CUTOFF

_lock = threading.Lock()
_cust_cache = {}         # mobile -> (fetched_at, bundle)
_art_cache = {}          # gen_art -> dict
_stores = {'at': 0, 'map': {}}
_maxdate = {'at': 0, 'value': None}


# ================================================================== helpers
def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _hhmm(secs):
    try:
        s = int(secs or 0)
    except (TypeError, ValueError):
        return ''
    h, m = (s // 3600) % 24, (s % 3600) // 60
    ampm = 'AM' if h < 12 else 'PM'
    h12 = h % 12 or 12
    return f'{h12:02d}:{m:02d} {ampm}'


def valid_mobile(mobile):
    m = str(mobile or '').strip()
    return (len(m) == 10 and m[0] in '6789' and m.isdigit()
            and m not in sf.JUNK_MOBILES)


# ================================================================== masters
def data_through(force=False):
    """Latest sales date visible to the app (live view)."""
    now = time.time()
    if force or now - _maxdate['at'] > 300:
        _maxdate['value'] = sf.max_trans_date(live=True)
        _maxdate['at'] = now
    return _maxdate['value']


def store_map(force=False):
    """{STORE_CODE: {'code','name','city','state'}} — DIM first, RETAIL_HUB gap-fill."""
    now = time.time()
    if not force and _stores['map'] and now - _stores['at'] < MASTER_TTL:
        return _stores['map']

    rows = sf.query("""
        WITH dim AS (
            SELECT UPPER(TRIM(STORE_CODE)) AS CD,
                   NULLIF(TRIM(COALESCE(STORE_FULL_NAME, STORE_NAME)), '') AS NM,
                   STATE
            FROM V2RETAIL.GOLD.DIM_STORE_V2),
        hub AS (
            SELECT UPPER(TRIM(CODE)) AS CD, NULLIF(TRIM(NAME), '') AS NM, CITY, STATE
            FROM V2RETAIL.RETAIL_HUB.STORES),
        sil AS (
            SELECT UPPER(TRIM(STORE_CODE)) AS CD, CITY
            FROM V2RETAIL.SILVER.DIM_STORE_V2)
        SELECT COALESCE(d.CD, h.CD)                     AS CD,
               COALESCE(d.NM, h.NM)                     AS NM,
               COALESCE(s.CITY, h.CITY)                 AS CITY,
               COALESCE(d.STATE, h.STATE)               AS STATE
        FROM dim d
        FULL OUTER JOIN hub h ON h.CD = d.CD
        LEFT JOIN sil s ON s.CD = COALESCE(d.CD, h.CD)
    """)
    out = {}
    for cd, nm, city, state in rows:
        if not cd:
            continue
        city = sf.norm_city(city)
        name = (nm or '').strip()
        name = sf.norm_city(name) if name else city or cd
        out[cd] = {'code': cd, 'name': name or cd, 'city': city,
                   'state': sf.norm_state(state)}
    _stores['map'], _stores['at'] = out, now
    return out


def store_label(code):
    st = store_map().get(str(code or '').strip().upper())
    if not st:
        return {'code': code, 'name': code, 'city': '', 'state': '',
                'label': f'V2 Retail · {code}'}
    bits = [b for b in (st['name'], st['state']) if b and b.lower() != (st['name'] or '').lower()]
    return {**st, 'label': 'V2 Retail · ' + ', '.join([st['name']] + bits[1:])}


# ================================================================== articles
_ART_BLANK = {'name': 'Store Item', 'spec': '', 'category': 'Other',
              'material_group': '', 'colour': '', 'size': '',
              'sub': 'Unlisted in product master'}


def article_info(codes):
    """{variant_code: {...}} with a process-wide cache.

    Keyed on the FULL variant code (13 digits: 10-digit gen art + 3-digit
    size/colour suffix), because that is the level DIM_ARTICLE_V2 carries COLOUR
    and SIZE at — 2.6M of its 3.8M rows have them. Joining on the 10-digit gen
    art alone silently returns the parent row, where both are blank, which is
    why the first receipts had no "Dark Blue · Size 28" line.
    """
    want = sorted({c for c in codes if c and c not in _art_cache})
    for batch in sf.in_chunks(want, 300):
        marks = ','.join(['(?)'] * len(batch))
        rows = sf.query(f"""
            SELECT g.C,
                   MAX(v.COLOR)                AS COLOUR,
                   MAX(v.SIZE)                 AS SZ,
                   MAX(v.MATERIAL_GROUP_DESC)  AS V_GRP,
                   MAX(v.ARTICLE_DESC)         AS V_DESC,
                   MAX(a.MATERIAL_GROUP_DESC)  AS A_GRP,
                   MAX(a.ARTICLE_DESC)         AS A_DESC,
                   MAX(t.MAJOR_CATEGORY)       AS MAJ_CAT,
                   MAX(t.DIVISION)             AS DIVISION,
                   MAX(m.PRODUCT)              AS PRODUCT
            FROM (SELECT COLUMN1 AS C FROM VALUES {marks}) g
            LEFT JOIN V2RETAIL.SILVER.DIM_ARTICLE_V2 v ON v.ARTICLE_CODE = g.C
            LEFT JOIN V2RETAIL.SILVER.DIM_ARTICLE_V2 a ON a.ARTICLE_CODE = LEFT(g.C, 10)
            LEFT JOIN V2RETAIL.CDP_WORK.TBL_ARTICLE   t ON t.GEN_ART      = LEFT(g.C, 10)
            LEFT JOIN V2RETAIL.CDP_WORK.ARTMASTER_EAN m ON m.SKU          = LEFT(g.C, 10)
            GROUP BY g.C
        """, batch)
        found = set()
        for (code, colour, size, v_grp, v_desc,
             a_grp, a_desc, maj_cat, division, product) in rows:
            found.add(code)
            grp = v_grp or a_grp
            colour = (colour or '').strip()
            size = (size or '').strip()
            if not (grp or maj_cat or product):
                _art_cache[code] = dict(_ART_BLANK)
                continue
            bits = []
            if colour:
                bits.append(colour.title())
            if size:
                bits.append(size if size.lower().endswith('y') else f'Size {size}')
            _art_cache[code] = {
                'name': catalog.product_name(grp, maj_cat, product, fallback=None),
                'spec': catalog.spec_line(v_desc or a_desc),
                'category': catalog.category_of(grp, division, maj_cat),
                'material_group': grp or maj_cat or product or '',
                'colour': colour.title(), 'size': size,
                'sub': ' · '.join(bits),
            }
        for code in batch:                    # never re-query a code the masters lack
            if code not in found:
                _art_cache[code] = dict(_ART_BLANK)
    return {c: _art_cache.get(c, _ART_BLANK) for c in codes}


# ================================================================== customer pull
def _fetch_lines(mobile, cutoff):
    return sf.query(f"""
        SELECT RECEIPTID, STORE, TRANSDATE, TRANSTIME, ITEMID,
               QUANTITY, PRICE, DISCOUNT, PAYMENTAMOUNT, STAFFID
        FROM {sf.SALES_LIVE}
        WHERE CUSTMOBILENO = ? AND TRANSDATE >= ?
        ORDER BY TRANSDATE DESC, TRANSTIME DESC
    """, [mobile, cutoff])


def exists(mobile):
    """True when this number has at least one V2 bill — the login gate."""
    if not valid_mobile(mobile):
        return False
    row = sf.query(
        f'SELECT 1 FROM {sf.SALES_LIVE} WHERE CUSTMOBILENO = ? LIMIT 1', [mobile])
    return bool(row)


def _membership(card_events, today):
    """Tier held, since-date, validity and upgrade path from card-SKU purchases."""
    if not card_events:
        return {'is_member': False, 'tier': None, 'rate': 0.0, 'cards': [],
                'since': None, 'valid_till': None, 'active': False,
                'next_tier': 'Silver', 'next_fee': TIER_FEE['Silver'],
                'fees_paid': 0.0}
    cards = sorted(card_events, key=lambda c: (c['date'], c['time']))
    current = max(cards, key=lambda c: (TIER_RANK[c['tier']], c['date']))
    latest_of_tier = max((c for c in cards if c['tier'] == current['tier']),
                         key=lambda c: c['date'])
    valid_till = latest_of_tier['date'] + dt.timedelta(days=CARD_VALIDITY_DAYS)
    nxt = NEXT_TIER[current['tier']]
    return {
        'is_member': True,
        'tier': current['tier'],
        'rate': TIER_RATE[current['tier']],
        'cards': [{'tier': c['tier'], 'date': c['date'].isoformat(),
                   'store': store_label(c['store'])['label'],
                   'fee': TIER_FEE[c['tier']]} for c in cards],
        'since': cards[0]['date'].isoformat(),
        'valid_till': valid_till.isoformat(),
        'active': valid_till >= today,
        'days_left': (valid_till - today).days,
        'next_tier': nxt,
        'next_fee': TIER_FEE[nxt] if nxt else None,
        'next_rate': TIER_RATE[nxt] if nxt else None,
        'fees_paid': float(sum(TIER_FEE[c['tier']] for c in cards)),
    }


def _member_discount(lines):
    """Member discount on one bill: goods lines whose discount matches a slab rate.

    Validated rule carried over from the membership-card reports: non-card line,
    price >= 2, |discount/price - tier_rate| < 0.005. Highest matching tier wins.
    """
    best_tier, total = None, 0.0
    for tier in ('Platinum', 'Gold', 'Silver'):
        rate = TIER_RATE[tier]
        hit = [ln for ln in lines
               if not ln['is_card'] and ln['price'] >= 2
               and abs((ln['discount'] / ln['price']) - rate) < RATE_TOL]
        if hit:
            best_tier = tier
            total = float(sum(ln['discount'] for ln in hit))
            break
    return best_tier, total


def build_bundle(mobile):
    """Everything the app needs for one shopper, in two Snowflake round trips."""
    cutoff = purchase_cutoff()
    raw = _fetch_lines(mobile, cutoff)
    today = data_through() or dt.date.today()

    arts = article_info({catalog.full_code(r[4]) for r in raw})

    bills, card_events = {}, []
    for (receipt, store, tdate, ttime, itemid, qty, price, disc, pay, staff) in raw:
        store = str(store or '').strip().upper()
        code = catalog.full_code(itemid)
        info = arts.get(code, _ART_BLANK)
        is_card = str(itemid) in CARD_SKUS
        # Bills older than ~Aug-2024 carry only the bill total: QUANTITY/PRICE/
        # DISCOUNT are NULL on every line. Those bills are shown as a verified
        # item list with the paid total, and flagged as not itemised.
        priced = price is not None
        qty, price, disc = _f(qty) or 1.0, _f(price), _f(disc)
        gross = price * qty
        net = gross - disc

        if is_card:
            tier = CARD_SKUS[str(itemid)]
            card_events.append({'tier': tier, 'date': tdate, 'time': ttime or 0,
                                'store': store})
            name = f'V2 VIP Membership Card · {tier}'
        else:
            name = info.get('name') or 'V2 Merchandise'

        b = bills.get(receipt)
        if b is None:
            st = store_label(store)
            b = bills[receipt] = {
                'receipt': receipt, 'store_code': store, 'store': st['label'],
                'store_name': st['name'], 'city': st['city'], 'state': st['state'],
                'date': tdate, 'time': ttime or 0, 'time_label': _hhmm(ttime),
                'cashier': staff or '', 'paid': _f(pay), 'lines': [],
            }
        b['lines'].append({
            'item': name,
            'spec': '' if is_card else info.get('spec', ''),
            'sub': 'V2 VIP Club membership' if is_card else info.get('sub', ''),
            'colour': '' if is_card else info.get('colour', ''),
            'size': '' if is_card else info.get('size', ''),
            'category': 'Membership' if is_card else info.get('category', 'Other'),
            'qty': qty, 'mrp': gross, 'price': price, 'discount': disc, 'net': net,
            'is_card': is_card, 'art': code, 'gen_art': code[:10], 'priced': priced,
            # a carry bag rings up at ₹1 and is discounted to zero. The bounds are
            # two-sided on purpose: a return line is also net<=0 but its gross is
            # negative, and it is merchandise, not a freebie.
            'complimentary': priced and (not is_card) and 0 <= gross <= 2 and net <= 0,
        })

    out = []
    for b in bills.values():
        # the ₹1 carry bag is discounted to zero on nearly every bill; counting it
        # would put a meaningless "saved ₹1" on the shopper's whole history
        goods = [ln for ln in b['lines'] if not ln['is_card'] and not ln['complimentary']]
        tier, mdisc = _member_discount(b['lines'])
        b['itemised'] = any(ln['priced'] for ln in b['lines'])
        b['gross'] = round(sum(ln['mrp'] for ln in goods), 2)
        b['discount'] = round(sum(ln['discount'] for ln in goods), 2)
        b['card_fee'] = round(sum(ln['net'] for ln in b['lines'] if ln['is_card']), 2)
        b['total'] = round(b['paid'], 2)
        # merchandise value of the bill — the card fee is not a purchase.
        # Returns come through as their own receipt (store code suffixed R*) with
        # a negative total and negative quantities; they net off, never clamp.
        b['net'] = round(b['total'] - b['card_fee'], 2)
        b['is_return'] = b['total'] < 0
        b['items'] = int(sum(ln['qty'] for ln in goods))
        b['member_tier'] = tier
        b['member_discount'] = round(mdisc, 2)
        b['saving_pct'] = round(100.0 * b['discount'] / b['gross'], 1) if b['gross'] else 0.0
        b['date_iso'] = b['date'].isoformat()
        b['date_label'] = b['date'].strftime('%d %b %Y')
        out.append(b)
    out.sort(key=lambda x: (x['date'], x['time']), reverse=True)

    membership = _membership(card_events, today)
    return {
        'mobile': mobile,
        'bills': out,
        'membership': membership,
        'summary': _summarise(out, membership, today),
        'data_through': today.isoformat(),
        'cutoff': cutoff.isoformat(),
        'fetched_at': dt.datetime.now().isoformat(timespec='seconds'),
    }


def _summarise(bills, membership, today):
    purchases = [b for b in bills if not b['is_return']]
    returns = [b for b in bills if b['is_return']]
    spend = sum(b['net'] for b in bills)          # net of returns
    saved = sum(b['discount'] for b in purchases)
    member_saved = sum(b['member_discount'] for b in bills)
    items = sum(b['items'] for b in bills)

    by_store, by_month, by_cat = {}, {}, {}
    for b in bills:
        s = by_store.setdefault(b['store_code'], {
            'code': b['store_code'], 'store': b['store'], 'bills': 0, 'spend': 0.0})
        s['bills'] += 1
        s['spend'] += b['net']
        m = by_month.setdefault(b['date'].strftime('%Y-%m'), {'month': b['date'].strftime('%Y-%m'),
                                                              'spend': 0.0, 'saved': 0.0, 'bills': 0})
        m['spend'] += b['net']
        m['saved'] += b['discount']
        m['bills'] += 1
        for ln in b['lines']:
            if ln['is_card'] or ln['complimentary']:
                continue
            c = by_cat.setdefault(ln['category'], {'category': ln['category'],
                                                   'spend': 0.0, 'items': 0})
            c['spend'] += ln['net']
            c['items'] += int(ln['qty'])

    first = min((b['date'] for b in bills), default=None)
    last = max((b['date'] for b in bills), default=None)
    fav = max(by_store.values(), key=lambda s: (s['spend'], s['bills']), default=None)

    # 12-month eligible spend drives the "what a card would have saved you" nudge
    cut = today - dt.timedelta(days=365)
    spend_12m = sum(b['net'] for b in bills if b['date'] >= cut)

    months = sorted(by_month.values(), key=lambda m: m['month'])
    itemised = [b for b in bills if b['itemised']]
    return {
        'bills': len(bills),
        'itemised_bills': len(itemised),
        'itemised_from': min((b['date'] for b in itemised), default=None) and
                         min(b['date'] for b in itemised).isoformat(),
        'spend': round(spend, 2),
        'saved': round(saved, 2),
        'member_saved': round(member_saved, 2),
        'items': items,
        'purchases': len(purchases),
        'returns': len(returns),
        'returned_value': round(abs(sum(b['net'] for b in returns)), 2),
        'atv': round(sum(b['net'] for b in purchases) / len(purchases), 2) if purchases else 0.0,
        'first_visit': first.isoformat() if first else None,
        'last_visit': last.isoformat() if last else None,
        'days_since_last': (today - last).days if last else None,
        'favourite_store': fav,
        'stores_visited': len(by_store),
        'by_store': sorted(by_store.values(), key=lambda s: -s['spend']),
        'by_month': months[-24:],
        'by_category': sorted(by_cat.values(), key=lambda c: -c['spend']),
        'spend_12m': round(spend_12m, 2),
        'card_opportunity': round(spend_12m * TIER_RATE['Gold'], 2) if not membership['is_member'] else 0.0,
        'card_roi': round(member_saved - membership['fees_paid'], 2) if membership['is_member'] else 0.0,
    }


# ================================================================== cache
def bundle(mobile, force=False):
    mobile = str(mobile).strip()
    now = time.time()
    with _lock:
        hit = _cust_cache.get(mobile)
        if hit and not force and now - hit[0] < CACHE_TTL:
            return hit[1]
    data = build_bundle(mobile)
    with _lock:
        _cust_cache[mobile] = (now, data)
        if len(_cust_cache) > 500:            # keep the process flat for a shared app
            for k, _ in sorted(_cust_cache.items(), key=lambda kv: kv[1][0])[:100]:
                _cust_cache.pop(k, None)
    return data


def invalidate(mobile):
    with _lock:
        _cust_cache.pop(str(mobile).strip(), None)


def health():
    t0 = time.time()
    d = data_through(force=True)
    return {'snowflake': 'ok', 'data_through': d.isoformat() if d else None,
            'stores': len(store_map()), 'ping_s': round(time.time() - t0, 2),
            'cached_customers': len(_cust_cache), 'cached_articles': len(_art_cache)}
