"""
sf_profiles.py — the customer-entered half of the profile, in Snowflake.

Snowflake has no customer master: it knows a mobile number bought things, never
who that number belongs to. This module creates and maintains the one table that
does — V2RETAIL.CDP_WORK.CUSTOMER_PROFILE — written by shoppers themselves
through the Customer App, so the details become real CRM data (campaign targeting,
birthday / anniversary triggers, email capture) instead of sitting on one machine.

Design notes:
  - Snowflake is the SOURCE OF TRUTH; the app's SQLite copy is a cache and an
    outbox. A save is written locally first (instant, never lost if the warehouse
    is asleep) and pushed here by a background worker — see store.py.
  - UPSERT is a MERGE on MOBILE. Snowflake does not enforce primary keys, so the
    MERGE is the thing that keeps one row per number.
  - UPDATED_AT is supplied by the caller, not CURRENT_TIMESTAMP(), so a replayed
    or retried sync cannot make an older edit look newer than it is.
  - Writes are confined to this one table. Nothing in the app can write to a
    sales object; every other Snowflake call in the app is read-only.
"""
import sys
import datetime as dt

sys.path.insert(0, r'E:\ANAND PRAKASH\AUTOMATION')
import sf_helper as sf  # noqa: E402

TABLE = 'V2RETAIL.CDP_WORK.CUSTOMER_PROFILE'
SOURCE = 'CUSTOMER_APP'

COLUMNS = ('NAME', 'EMAIL', 'DOB', 'ANNIVERSARY', 'GENDER',
           'PREFERRED_STORE', 'MARKETING_OPT_IN')

_ensured = False


def ensure_table():
    """Create the table on first use. Idempotent — safe on every boot."""
    global _ensured
    if _ensured:
        return
    sf.query(f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            MOBILE           VARCHAR(10)   NOT NULL,
            NAME             VARCHAR(120),
            EMAIL            VARCHAR(255),
            DOB              DATE,
            ANNIVERSARY      DATE,
            GENDER           VARCHAR(20),
            PREFERRED_STORE  VARCHAR(20),
            MARKETING_OPT_IN BOOLEAN       DEFAULT FALSE,
            SOURCE           VARCHAR(40),
            CREATED_AT       TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
            UPDATED_AT       TIMESTAMP_NTZ
        )
        COMMENT = 'Customer-entered profile details captured by the V2 Customer App
                   (E:\\ANAND PRAKASH\\AUTOMATION\\Customer App). One row per mobile,
                   maintained by MERGE. The warehouse has no other customer master.'
    """)
    _ensured = True


def _ts(value):
    """ISO string -> 'YYYY-MM-DD HH:MM:SS' (TO_TIMESTAMP_NTZ dislikes the 'T')."""
    if isinstance(value, dt.datetime):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    s = str(value or '').replace('T', ' ')
    return s[:19] or dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def upsert(mobile, profile):
    """Write one shopper's details. Raises on failure so the caller can requeue."""
    ensure_table()
    opt_in = profile.get('marketing_opt_in')
    binds = [
        str(mobile),
        profile.get('name') or None,
        profile.get('email') or None,
        profile.get('dob') or None,
        profile.get('anniversary') or None,
        profile.get('gender') or None,
        profile.get('preferred_store') or None,
        1 if opt_in else 0,
        SOURCE,
        _ts(profile.get('updated_at')),
    ]
    sf.query(f"""
        MERGE INTO {TABLE} t
        USING (SELECT ? AS MOBILE, ? AS NAME, ? AS EMAIL,
                      TO_DATE(?) AS DOB, TO_DATE(?) AS ANNIVERSARY,
                      ? AS GENDER, ? AS PREFERRED_STORE,
                      TO_BOOLEAN(?) AS MARKETING_OPT_IN, ? AS SOURCE,
                      TO_TIMESTAMP_NTZ(?) AS UPDATED_AT) s
        ON t.MOBILE = s.MOBILE
        WHEN MATCHED THEN UPDATE SET
            t.NAME = s.NAME, t.EMAIL = s.EMAIL, t.DOB = s.DOB,
            t.ANNIVERSARY = s.ANNIVERSARY, t.GENDER = s.GENDER,
            t.PREFERRED_STORE = s.PREFERRED_STORE,
            t.MARKETING_OPT_IN = s.MARKETING_OPT_IN,
            t.SOURCE = s.SOURCE, t.UPDATED_AT = s.UPDATED_AT
        WHEN NOT MATCHED THEN INSERT
            (MOBILE, NAME, EMAIL, DOB, ANNIVERSARY, GENDER, PREFERRED_STORE,
             MARKETING_OPT_IN, SOURCE, CREATED_AT, UPDATED_AT)
            VALUES (s.MOBILE, s.NAME, s.EMAIL, s.DOB, s.ANNIVERSARY, s.GENDER,
                    s.PREFERRED_STORE, s.MARKETING_OPT_IN, s.SOURCE,
                    CURRENT_TIMESTAMP(), s.UPDATED_AT)
    """, binds)
    return True


def fetch(mobile):
    """Read one shopper's stored details, or None. Used to re-seed a fresh host."""
    ensure_table()
    rows = sf.query(f"""
        SELECT NAME, EMAIL, TO_CHAR(DOB,'YYYY-MM-DD'),
               TO_CHAR(ANNIVERSARY,'YYYY-MM-DD'), GENDER, PREFERRED_STORE,
               MARKETING_OPT_IN, TO_CHAR(UPDATED_AT,'YYYY-MM-DD"T"HH24:MI:SS')
        FROM {TABLE} WHERE MOBILE = ?
    """, [str(mobile)])
    if not rows:
        return None
    name, email, dob, anniv, gender, store, opt_in, updated = rows[0]
    return {'mobile': str(mobile), 'name': name, 'email': email, 'dob': dob,
            'anniversary': anniv, 'gender': gender, 'preferred_store': store,
            'marketing_opt_in': bool(opt_in), 'updated_at': updated}


def stats():
    """Coverage numbers — handy for the health endpoint and for CRM planning."""
    ensure_table()
    row = sf.query(f"""
        SELECT COUNT(*),
               COUNT(NAME), COUNT(EMAIL), COUNT(DOB),
               SUM(IFF(MARKETING_OPT_IN, 1, 0)),
               TO_CHAR(MAX(UPDATED_AT),'YYYY-MM-DD HH24:MI:SS')
        FROM {TABLE}
    """)[0]
    return {'profiles': row[0], 'with_name': row[1], 'with_email': row[2],
            'with_dob': row[3], 'opted_in': int(row[4] or 0), 'last_write': row[5]}
