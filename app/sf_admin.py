"""
sf_admin.py — the admin-managed side of the app, stored in Snowflake.

Four tables, all in V2RETAIL.CDP_WORK, all created on first boot:

  APP_OFFER           the running offers a shopper sees (Bill Buster, Spin The
                      Wheel, Selfie, 70% Off, ...). Scope ALL or specific stores.
  APP_OFFER_STORE     offer -> store codes, when an offer is store-specific.
  APP_STORE_LOCATION  admin-maintained lat/long, address, phone and hours.
                      Layered OVER RETAIL_HUB.STORE_LOCATION_MASTER, which covers
                      387 stores (373 of 439 operational) — the gaps and any
                      corrections are entered here.
  APP_NUDGE           a message pushed to customers, with an audience RULE.

Why a nudge stores a rule and not a recipient list: "all customers" is ~13M
mobiles. Materialising that per nudge would be millions of rows for a message
most of them never open. The rule is evaluated in Python against the shopper
already loaded at request time (free), and only the READ/dismiss event is
written back, to APP_NUDGE_READ.
"""
import sys
import json
import datetime as dt

sys.path.insert(0, r'E:\ANAND PRAKASH\AUTOMATION')
import sf_helper as sf  # noqa: E402

SCHEMA = 'V2RETAIL.CDP_WORK'
T_OFFER = f'{SCHEMA}.APP_OFFER'
T_OFFER_STORE = f'{SCHEMA}.APP_OFFER_STORE'
T_LOCATION = f'{SCHEMA}.APP_STORE_LOCATION'
T_NUDGE = f'{SCHEMA}.APP_NUDGE'
T_NUDGE_READ = f'{SCHEMA}.APP_NUDGE_READ'

OFFER_TYPES = ('BILL_BUSTER', 'SPIN_WHEEL', 'SELFIE', 'DISCOUNT',
               'MEMBERSHIP', 'FESTIVAL', 'OTHER')
AUDIENCES = ('ALL', 'MEMBER', 'NON_MEMBER', 'TIER', 'STORE', 'LAPSED',
             'ACTIVE', 'MOBILES')

_ready = False


def ensure_tables():
    global _ready
    if _ready:
        return
    sf.query(f"""
        CREATE TABLE IF NOT EXISTS {T_OFFER} (
            OFFER_ID     VARCHAR(60) NOT NULL,
            TITLE        VARCHAR(120),
            SUBTITLE     VARCHAR(200),
            OFFER_TYPE   VARCHAR(30),
            BADGE        VARCHAR(40),
            ICON         VARCHAR(16),
            DESCRIPTION  VARCHAR(2000),
            MECHANIC     VARCHAR(2000),
            TERMS        VARCHAR(2000),
            MIN_BILL     NUMBER(12,2),
            SEGMENT      VARCHAR(30) DEFAULT 'ALL',
            SCOPE        VARCHAR(10) DEFAULT 'ALL',
            VALID_FROM   DATE,
            VALID_TO     DATE,
            PRIORITY     NUMBER(5,0) DEFAULT 100,
            IS_ACTIVE    BOOLEAN DEFAULT TRUE,
            CREATED_AT   TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
            UPDATED_AT   TIMESTAMP_NTZ,
            UPDATED_BY   VARCHAR(60))
        COMMENT = 'Customer-app offer catalogue, maintained from the app admin panel.'
    """)
    sf.query(f"""
        CREATE TABLE IF NOT EXISTS {T_OFFER_STORE} (
            OFFER_ID VARCHAR(60) NOT NULL,
            STORE_CODE VARCHAR(20) NOT NULL)
    """)
    sf.query(f"""
        CREATE TABLE IF NOT EXISTS {T_LOCATION} (
            STORE_CODE VARCHAR(20) NOT NULL,
            STORE_NAME VARCHAR(120),
            CITY       VARCHAR(80),
            STATE      VARCHAR(80),
            LATITUDE   FLOAT,
            LONGITUDE  FLOAT,
            ADDRESS    VARCHAR(500),
            PINCODE    VARCHAR(10),
            PHONE      VARCHAR(40),
            OPEN_TIME  VARCHAR(10),
            CLOSE_TIME VARCHAR(10),
            IS_ACTIVE  BOOLEAN DEFAULT TRUE,
            UPDATED_AT TIMESTAMP_NTZ,
            UPDATED_BY VARCHAR(60))
        COMMENT = 'THE store list for the V2 Customer App, uploaded as Excel from the
                   admin panel. The app shows these stores and only these stores.'
    """)
    # columns added after the first release — bring an older table up to date
    have = {r[0].upper() for r in sf.query(
        "SELECT COLUMN_NAME FROM V2RETAIL.INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = 'CDP_WORK' AND TABLE_NAME = 'APP_STORE_LOCATION'")}
    for col, decl in (('STORE_NAME', 'VARCHAR(120)'), ('CITY', 'VARCHAR(80)'),
                      ('STATE', 'VARCHAR(80)')):
        if col not in have:
            sf.query(f'ALTER TABLE {T_LOCATION} ADD COLUMN {col} {decl}')
    sf.query(f"""
        CREATE TABLE IF NOT EXISTS {T_NUDGE} (
            NUDGE_ID       VARCHAR(60) NOT NULL,
            TITLE          VARCHAR(120),
            BODY           VARCHAR(1000),
            CTA_LABEL      VARCHAR(60),
            CTA_TAB        VARCHAR(30),
            AUDIENCE_TYPE  VARCHAR(30),
            AUDIENCE_VALUE VARCHAR(4000),
            OFFER_ID       VARCHAR(60),
            STATUS         VARCHAR(20) DEFAULT 'DRAFT',
            EST_RECIPIENTS NUMBER(12,0),
            VALID_TO       DATE,
            CREATED_AT     TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
            SENT_AT        TIMESTAMP_NTZ,
            CREATED_BY     VARCHAR(60))
        COMMENT = 'Customer-app nudges. Audience is a RULE, evaluated per shopper at read time.'
    """)
    sf.query(f"""
        CREATE TABLE IF NOT EXISTS {T_NUDGE_READ} (
            NUDGE_ID  VARCHAR(60) NOT NULL,
            MOBILE    VARCHAR(10) NOT NULL,
            READ_AT   TIMESTAMP_NTZ,
            DISMISSED BOOLEAN DEFAULT FALSE)
    """)
    _ready = True


def _now():
    return dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _slug(prefix):
    return f"{prefix}-{dt.datetime.now().strftime('%y%m%d%H%M%S')}"


# ================================================================== offers
OFFER_COLS = ('OFFER_ID', 'TITLE', 'SUBTITLE', 'OFFER_TYPE', 'BADGE', 'ICON',
              'DESCRIPTION', 'MECHANIC', 'TERMS', 'MIN_BILL', 'SEGMENT', 'SCOPE',
              'VALID_FROM', 'VALID_TO', 'PRIORITY', 'IS_ACTIVE')


def _offer_row(r):
    return {
        'offer_id': r[0], 'title': r[1], 'subtitle': r[2], 'offer_type': r[3],
        'badge': r[4], 'icon': r[5], 'description': r[6], 'mechanic': r[7],
        'terms': r[8], 'min_bill': float(r[9]) if r[9] is not None else None,
        'segment': r[10], 'scope': r[11],
        'valid_from': str(r[12]) if r[12] else None,
        'valid_to': str(r[13]) if r[13] else None,
        'priority': int(r[14] or 100), 'is_active': bool(r[15]),
        'stores': [],
    }


def list_offers(active_only=False, on_date=None):
    ensure_tables()
    where, binds = [], []
    if active_only:
        where.append('IS_ACTIVE = TRUE')
        d = on_date or dt.date.today()
        where.append('(VALID_FROM IS NULL OR VALID_FROM <= ?)')
        where.append('(VALID_TO   IS NULL OR VALID_TO   >= ?)')
        binds += [d, d]
    sql = f"SELECT {', '.join(OFFER_COLS)} FROM {T_OFFER}"
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY PRIORITY, TITLE'
    offers = [_offer_row(r) for r in sf.query(sql, binds or None)]

    by_id = {o['offer_id']: o for o in offers}
    if by_id:
        for oid, st in sf.query(f'SELECT OFFER_ID, STORE_CODE FROM {T_OFFER_STORE}'):
            if oid in by_id:
                by_id[oid]['stores'].append(st)
    return offers


def save_offer(data, by='admin'):
    ensure_tables()
    oid = (data.get('offer_id') or '').strip() or _slug('OFF')
    binds = [
        oid, data.get('title'), data.get('subtitle'),
        (data.get('offer_type') or 'OTHER').upper(), data.get('badge'),
        data.get('icon') or '🎁', data.get('description'), data.get('mechanic'),
        data.get('terms'),
        data.get('min_bill') if data.get('min_bill') not in ('', None) else None,
        (data.get('segment') or 'ALL').upper(),
        (data.get('scope') or 'ALL').upper(),
        data.get('valid_from') or None, data.get('valid_to') or None,
        int(data.get('priority') or 100),
        1 if data.get('is_active', True) else 0,
        by, _now(),
    ]
    sf.query(f"""
        MERGE INTO {T_OFFER} t
        USING (SELECT ? AS OFFER_ID, ? AS TITLE, ? AS SUBTITLE, ? AS OFFER_TYPE,
                      ? AS BADGE, ? AS ICON, ? AS DESCRIPTION, ? AS MECHANIC,
                      ? AS TERMS, ? AS MIN_BILL, ? AS SEGMENT, ? AS SCOPE,
                      TO_DATE(?) AS VALID_FROM, TO_DATE(?) AS VALID_TO,
                      ? AS PRIORITY, TO_BOOLEAN(?) AS IS_ACTIVE,
                      ? AS UPDATED_BY, TO_TIMESTAMP_NTZ(?) AS UPDATED_AT) s
        ON t.OFFER_ID = s.OFFER_ID
        WHEN MATCHED THEN UPDATE SET
            t.TITLE=s.TITLE, t.SUBTITLE=s.SUBTITLE, t.OFFER_TYPE=s.OFFER_TYPE,
            t.BADGE=s.BADGE, t.ICON=s.ICON, t.DESCRIPTION=s.DESCRIPTION,
            t.MECHANIC=s.MECHANIC, t.TERMS=s.TERMS, t.MIN_BILL=s.MIN_BILL,
            t.SEGMENT=s.SEGMENT, t.SCOPE=s.SCOPE, t.VALID_FROM=s.VALID_FROM,
            t.VALID_TO=s.VALID_TO, t.PRIORITY=s.PRIORITY, t.IS_ACTIVE=s.IS_ACTIVE,
            t.UPDATED_BY=s.UPDATED_BY, t.UPDATED_AT=s.UPDATED_AT
        WHEN NOT MATCHED THEN INSERT
            (OFFER_ID, TITLE, SUBTITLE, OFFER_TYPE, BADGE, ICON, DESCRIPTION,
             MECHANIC, TERMS, MIN_BILL, SEGMENT, SCOPE, VALID_FROM, VALID_TO,
             PRIORITY, IS_ACTIVE, CREATED_AT, UPDATED_AT, UPDATED_BY)
            VALUES (s.OFFER_ID, s.TITLE, s.SUBTITLE, s.OFFER_TYPE, s.BADGE, s.ICON,
                    s.DESCRIPTION, s.MECHANIC, s.TERMS, s.MIN_BILL, s.SEGMENT,
                    s.SCOPE, s.VALID_FROM, s.VALID_TO, s.PRIORITY, s.IS_ACTIVE,
                    CURRENT_TIMESTAMP(), s.UPDATED_AT, s.UPDATED_BY)
    """, binds)

    stores = [str(s).strip().upper() for s in (data.get('stores') or []) if str(s).strip()]
    sf.query(f'DELETE FROM {T_OFFER_STORE} WHERE OFFER_ID = ?', [oid])
    if stores and (data.get('scope') or 'ALL').upper() == 'STORES':
        for batch in sf.in_chunks(stores, 500):
            vals = ','.join(['(?, ?)'] * len(batch))
            flat = []
            for s in batch:
                flat += [oid, s]
            sf.query(f'INSERT INTO {T_OFFER_STORE} (OFFER_ID, STORE_CODE) VALUES {vals}', flat)
    return oid


def delete_offer(offer_id):
    ensure_tables()
    sf.query(f'DELETE FROM {T_OFFER_STORE} WHERE OFFER_ID = ?', [offer_id])
    sf.query(f'DELETE FROM {T_OFFER} WHERE OFFER_ID = ?', [offer_id])


# ================================================================== locations
def list_stores():
    """THE store list — exactly what the uploaded sheet holds, nothing else."""
    ensure_tables()
    rows = sf.query(f"""SELECT STORE_CODE, STORE_NAME, CITY, STATE,
                               LATITUDE, LONGITUDE, ADDRESS, PINCODE,
                               PHONE, OPEN_TIME, CLOSE_TIME, IS_ACTIVE
                        FROM {T_LOCATION}""")
    return {r[0]: {'code': r[0], 'name': r[1], 'city': r[2], 'state': r[3],
                   'lat': r[4], 'lng': r[5], 'address': r[6], 'pincode': r[7],
                   'phone': r[8], 'open_time': r[9], 'close_time': r[10],
                   'is_active': bool(r[11])} for r in rows}


def save_location(code, data, by='admin'):
    ensure_tables()
    code = str(code).strip().upper()
    lat, lng = data.get('lat'), data.get('lng')
    lat = float(lat) if lat not in ('', None) else None
    lng = float(lng) if lng not in ('', None) else None
    if lat is not None and not (6 <= lat <= 38):
        raise ValueError('Latitude must be between 6 and 38 (India).')
    if lng is not None and not (68 <= lng <= 98):
        raise ValueError('Longitude must be between 68 and 98 (India).')
    sf.query(f"""
        MERGE INTO {T_LOCATION} t
        USING (SELECT ? AS STORE_CODE, ? AS STORE_NAME, ? AS CITY, ? AS STATE,
                      ? AS LATITUDE, ? AS LONGITUDE, ? AS ADDRESS,
                      ? AS PINCODE, ? AS PHONE, ? AS OPEN_TIME, ? AS CLOSE_TIME,
                      TO_BOOLEAN(?) AS IS_ACTIVE, ? AS UPDATED_BY,
                      TO_TIMESTAMP_NTZ(?) AS UPDATED_AT) s
        ON t.STORE_CODE = s.STORE_CODE
        WHEN MATCHED THEN UPDATE SET
            t.STORE_NAME=s.STORE_NAME, t.CITY=s.CITY, t.STATE=s.STATE,
            t.LATITUDE=s.LATITUDE, t.LONGITUDE=s.LONGITUDE, t.ADDRESS=s.ADDRESS,
            t.PINCODE=s.PINCODE, t.PHONE=s.PHONE, t.OPEN_TIME=s.OPEN_TIME,
            t.CLOSE_TIME=s.CLOSE_TIME, t.IS_ACTIVE=s.IS_ACTIVE,
            t.UPDATED_BY=s.UPDATED_BY, t.UPDATED_AT=s.UPDATED_AT
        WHEN NOT MATCHED THEN INSERT
            (STORE_CODE, STORE_NAME, CITY, STATE, LATITUDE, LONGITUDE, ADDRESS,
             PINCODE, PHONE, OPEN_TIME, CLOSE_TIME, IS_ACTIVE, UPDATED_AT, UPDATED_BY)
            VALUES (s.STORE_CODE, s.STORE_NAME, s.CITY, s.STATE, s.LATITUDE,
                    s.LONGITUDE, s.ADDRESS, s.PINCODE, s.PHONE, s.OPEN_TIME,
                    s.CLOSE_TIME, s.IS_ACTIVE, s.UPDATED_AT, s.UPDATED_BY)
    """, [code, data.get('name'), data.get('city'), data.get('state'),
          lat, lng, data.get('address'), data.get('pincode'),
          data.get('phone'), data.get('open_time'), data.get('close_time'),
          1 if data.get('is_active', True) else 0, by, _now()])
    return code


def delete_location(code):
    ensure_tables()
    sf.query(f'DELETE FROM {T_LOCATION} WHERE STORE_CODE = ?',
             [str(code).strip().upper()])


def base_locations():
    """Warehouse geo + address. Used ONLY to pre-fill the downloadable template
    so the CRM team starts from a list instead of a blank page. Nothing the app
    renders comes from here — the uploaded sheet is the store list."""
    ensure_tables()
    rows = sf.query("""
        WITH loc AS (
            SELECT UPPER(TRIM(STORE_CODE)) AS CD,
                   MAX(LATITUDE) AS LA, MAX(LONGITUDE) AS LO, MAX(PIN_CODE) AS PIN
            FROM V2RETAIL.RETAIL_HUB.STORE_LOCATION_MASTER GROUP BY 1),
        hub AS (
            SELECT UPPER(TRIM(CODE)) AS CD, MAX(ADDRESS) AS ADDR,
                   MAX(PINCODE) AS PIN, MAX(CONTACT) AS PHONE
            FROM V2RETAIL.RETAIL_HUB.STORES GROUP BY 1)
        SELECT COALESCE(l.CD, h.CD),
               IFF(l.LA BETWEEN 6 AND 38 AND l.LO BETWEEN 68 AND 98, l.LA, NULL),
               IFF(l.LA BETWEEN 6 AND 38 AND l.LO BETWEEN 68 AND 98, l.LO, NULL),
               h.ADDR, COALESCE(l.PIN, h.PIN), h.PHONE
        FROM loc l FULL OUTER JOIN hub h ON h.CD = l.CD
    """)
    return {r[0]: {'lat': r[1], 'lng': r[2], 'address': r[3],
                   'pincode': r[4], 'phone': r[5]} for r in rows if r[0]}


# ================================================================== nudges
def list_nudges(sent_only=False, on_date=None):
    ensure_tables()
    where, binds = [], []
    if sent_only:
        where.append("STATUS = 'SENT'")
        d = on_date or dt.date.today()
        where.append('(VALID_TO IS NULL OR VALID_TO >= ?)')
        binds.append(d)
    sql = (f"SELECT NUDGE_ID, TITLE, BODY, CTA_LABEL, CTA_TAB, AUDIENCE_TYPE, "
           f"AUDIENCE_VALUE, OFFER_ID, STATUS, EST_RECIPIENTS, "
           f"TO_CHAR(VALID_TO,'YYYY-MM-DD'), TO_CHAR(CREATED_AT,'YYYY-MM-DD HH24:MI'), "
           f"TO_CHAR(SENT_AT,'YYYY-MM-DD HH24:MI') FROM {T_NUDGE}")
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY COALESCE(SENT_AT, CREATED_AT) DESC'
    return [{'nudge_id': r[0], 'title': r[1], 'body': r[2], 'cta_label': r[3],
             'cta_tab': r[4], 'audience_type': r[5], 'audience_value': r[6],
             'offer_id': r[7], 'status': r[8],
             'est_recipients': int(r[9] or 0), 'valid_to': r[10],
             'created_at': r[11], 'sent_at': r[12]} for r in sf.query(sql, binds or None)]


def save_nudge(data, by='admin', send=False):
    ensure_tables()
    nid = (data.get('nudge_id') or '').strip() or _slug('NDG')
    status = 'SENT' if send else (data.get('status') or 'DRAFT').upper()
    sent_at = _now() if send else None
    binds = [nid, data.get('title'), data.get('body'), data.get('cta_label'),
             data.get('cta_tab') or 'offers',
             (data.get('audience_type') or 'ALL').upper(),
             str(data.get('audience_value') or '')[:4000],
             data.get('offer_id') or None, status,
             int(data.get('est_recipients') or 0),
             data.get('valid_to') or None, by, sent_at]
    sf.query(f"""
        MERGE INTO {T_NUDGE} t
        USING (SELECT ? AS NUDGE_ID, ? AS TITLE, ? AS BODY, ? AS CTA_LABEL,
                      ? AS CTA_TAB, ? AS AUDIENCE_TYPE, ? AS AUDIENCE_VALUE,
                      ? AS OFFER_ID, ? AS STATUS, ? AS EST_RECIPIENTS,
                      TO_DATE(?) AS VALID_TO, ? AS CREATED_BY,
                      TO_TIMESTAMP_NTZ(?) AS SENT_AT) s
        ON t.NUDGE_ID = s.NUDGE_ID
        WHEN MATCHED THEN UPDATE SET
            t.TITLE=s.TITLE, t.BODY=s.BODY, t.CTA_LABEL=s.CTA_LABEL,
            t.CTA_TAB=s.CTA_TAB, t.AUDIENCE_TYPE=s.AUDIENCE_TYPE,
            t.AUDIENCE_VALUE=s.AUDIENCE_VALUE, t.OFFER_ID=s.OFFER_ID,
            t.STATUS=s.STATUS, t.EST_RECIPIENTS=s.EST_RECIPIENTS,
            t.VALID_TO=s.VALID_TO, t.SENT_AT=COALESCE(s.SENT_AT, t.SENT_AT)
        WHEN NOT MATCHED THEN INSERT
            (NUDGE_ID, TITLE, BODY, CTA_LABEL, CTA_TAB, AUDIENCE_TYPE,
             AUDIENCE_VALUE, OFFER_ID, STATUS, EST_RECIPIENTS, VALID_TO,
             CREATED_AT, SENT_AT, CREATED_BY)
            VALUES (s.NUDGE_ID, s.TITLE, s.BODY, s.CTA_LABEL, s.CTA_TAB,
                    s.AUDIENCE_TYPE, s.AUDIENCE_VALUE, s.OFFER_ID, s.STATUS,
                    s.EST_RECIPIENTS, s.VALID_TO, CURRENT_TIMESTAMP(),
                    s.SENT_AT, s.CREATED_BY)
    """, binds)
    return nid


def delete_nudge(nudge_id):
    ensure_tables()
    sf.query(f'DELETE FROM {T_NUDGE_READ} WHERE NUDGE_ID = ?', [nudge_id])
    sf.query(f'DELETE FROM {T_NUDGE} WHERE NUDGE_ID = ?', [nudge_id])


def mark_nudge_read(nudge_id, mobile, dismissed=False):
    ensure_tables()
    sf.query(f"""
        MERGE INTO {T_NUDGE_READ} t
        USING (SELECT ? AS NUDGE_ID, ? AS MOBILE, TO_TIMESTAMP_NTZ(?) AS READ_AT,
                      TO_BOOLEAN(?) AS DISMISSED) s
        ON t.NUDGE_ID = s.NUDGE_ID AND t.MOBILE = s.MOBILE
        WHEN MATCHED THEN UPDATE SET
            t.READ_AT = COALESCE(t.READ_AT, s.READ_AT),
            t.DISMISSED = t.DISMISSED OR s.DISMISSED
        WHEN NOT MATCHED THEN INSERT (NUDGE_ID, MOBILE, READ_AT, DISMISSED)
            VALUES (s.NUDGE_ID, s.MOBILE, s.READ_AT, s.DISMISSED)
    """, [nudge_id, str(mobile), _now(), 1 if dismissed else 0])


def nudge_state_for(mobile):
    """{nudge_id: {'read': bool, 'dismissed': bool}} for one shopper."""
    ensure_tables()
    return {r[0]: {'read': r[1] is not None, 'dismissed': bool(r[2])}
            for r in sf.query(
                f'SELECT NUDGE_ID, READ_AT, DISMISSED FROM {T_NUDGE_READ} WHERE MOBILE = ?',
                [str(mobile)])}


def nudge_stats():
    """Opened / dismissed counts per nudge, for the admin list."""
    ensure_tables()
    return {r[0]: {'opened': int(r[1] or 0), 'dismissed': int(r[2] or 0)}
            for r in sf.query(f"""
                SELECT NUDGE_ID, COUNT(READ_AT), SUM(IFF(DISMISSED,1,0))
                FROM {T_NUDGE_READ} GROUP BY 1""")}


# ================================================================== audience
def estimate_audience(audience_type, audience_value):
    """How many distinct mobiles a nudge rule would reach. Counted against the
    live sales view so the admin sees a real number before sending."""
    ensure_tables()
    a = (audience_type or 'ALL').upper()
    v = str(audience_value or '').strip()
    card_skus = "'000000001730000018','000000001730000003','000000001730000004'"

    if a == 'MOBILES':
        return len({m.strip() for m in v.replace('\n', ',').split(',') if m.strip()})

    base = f"SELECT DISTINCT CUSTMOBILENO FROM {sf.SALES_LIVE} WHERE {sf.VALID_MOBILE_SQL}"
    if a == 'ALL':
        sql = f'SELECT COUNT(*) FROM ({base})'
        binds = []
    elif a == 'STORE':
        sql = f"SELECT COUNT(*) FROM ({base} AND STORE = ?)"
        binds = [v.upper()]
    elif a == 'ACTIVE':
        days = int(v or 90)
        sql = (f'SELECT COUNT(*) FROM ({base} AND TRANSDATE >= '
               f'DATEADD(day,-{days},CURRENT_DATE()))')
        binds = []
    elif a == 'LAPSED':
        days = int(v or 180)
        sql = f"""SELECT COUNT(*) FROM (
                    SELECT CUSTMOBILENO FROM {sf.SALES_LIVE}
                    WHERE {sf.VALID_MOBILE_SQL}
                    GROUP BY 1 HAVING MAX(TRANSDATE) <
                        DATEADD(day,-{days},CURRENT_DATE()))"""
        binds = []
    elif a in ('MEMBER', 'TIER'):
        sku = {'Silver': "'000000001730000018'", 'Gold': "'000000001730000003'",
               'Platinum': "'000000001730000004'"}.get(v, card_skus)
        sql = (f'SELECT COUNT(DISTINCT CUSTMOBILENO) FROM {sf.SALES_LIVE} '
               f'WHERE ITEMID IN ({sku}) AND {sf.VALID_MOBILE_SQL}')
        binds = []
    elif a == 'NON_MEMBER':
        sql = f"""SELECT COUNT(*) FROM ({base}) b
                  WHERE b.CUSTMOBILENO NOT IN (
                      SELECT DISTINCT CUSTMOBILENO FROM {sf.SALES_LIVE}
                      WHERE ITEMID IN ({card_skus}))"""
        binds = []
    else:
        return 0
    return int(sf.query(sql, binds or None)[0][0] or 0)


def admin_overview():
    ensure_tables()
    offers = sf.query(f'SELECT COUNT(*), SUM(IFF(IS_ACTIVE,1,0)) FROM {T_OFFER}')[0]
    nudges = sf.query(f"SELECT COUNT(*), SUM(IFF(STATUS='SENT',1,0)) FROM {T_NUDGE}")[0]
    reads = sf.query(f'SELECT COUNT(*), COUNT(DISTINCT MOBILE) FROM {T_NUDGE_READ}')[0]
    locs = sf.query(f'SELECT COUNT(*) FROM {T_LOCATION}')[0]
    return {'offers': int(offers[0] or 0), 'offers_active': int(offers[1] or 0),
            'nudges': int(nudges[0] or 0), 'nudges_sent': int(nudges[1] or 0),
            'nudge_opens': int(reads[0] or 0), 'nudge_readers': int(reads[1] or 0),
            'location_overrides': int(locs[0] or 0)}
