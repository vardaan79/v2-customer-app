"""
store_sheet.py — store locations by Excel upload.

The warehouse's RETAIL_HUB.STORE_LOCATION_MASTER covers only 387 stores, leaves
~65 trading stores with no pin at all, and carries a dozen coordinates that land
in the wrong state (HM58 'Gwalior' sits on Delhi). Rather than depend on it, the
CRM team maintains the list in Excel and uploads it here; the upload becomes the
source of truth in APP_STORE_LOCATION.

Flow:
  1. Admin downloads a template — pre-filled with every store and whatever
     coordinates we already hold, so it is a correction sheet, not a blank page.
  2. Admin edits and uploads it.
  3. The file is PARSED AND PREVIEWED first (what would change, what is broken).
     Nothing is written until they confirm.

Header matching is forgiving on purpose: a sheet exported from anywhere tends to
say STORE CODE or ST_CD or Code, LAT or LATITUDE or Lat/Long. Anything it cannot
map is reported by name instead of being silently dropped.
"""
import io
import re
import datetime as dt  # noqa: F401  (used by _clock for time/datetime cells)

from openpyxl import Workbook, load_workbook

import sf_admin

# header text (lowercased, stripped of non-letters) -> our field
ALIASES = {
    'storecode': 'code', 'stcd': 'code', 'code': 'code', 'store': 'code',
    'storeid': 'code', 'sitecode': 'code', 'stcode': 'code',
    'storename': 'name', 'name': 'name', 'stnm': 'name', 'stfullnm': 'name',
    'city': 'city', 'town': 'city', 'cityname': 'city', 'location': 'city',
    'state': 'state', 'statename': 'state', 'province': 'state',
    'latitude': 'lat', 'lat': 'lat', 'storelat': 'lat',
    'longitude': 'lng', 'long': 'lng', 'lng': 'lng', 'lon': 'lng',
    'storelong': 'lng',
    # a single column holding "22.1167, 84.0333" or a pasted Maps link — very
    # common in hand-kept sheets, and the lat parser already splits it
    'latlong': 'lat', 'latlng': 'lat', 'latitudelongitude': 'lat',
    'coordinates': 'lat', 'coordinate': 'lat', 'coords': 'lat', 'geo': 'lat',
    'geolocation': 'lat', 'maplink': 'lat', 'mapslink': 'lat',
    'googlemaplink': 'lat', 'googlemapslink': 'lat', 'maps': 'lat',
    # NB: a bare "Location" column means the town, not coordinates — it maps to
    # city below. Only the explicitly geographic spellings land on lat.
    'address': 'address', 'storeaddress': 'address', 'fulladdress': 'address',
    'pincode': 'pincode', 'pin': 'pincode', 'postalcode': 'pincode',
    'zip': 'pincode',
    'phone': 'phone', 'contact': 'phone', 'mobile': 'phone',
    'contactnumber': 'phone', 'phonenumber': 'phone',
    'opentime': 'open_time', 'opens': 'open_time', 'opensat': 'open_time',
    'openingtime': 'open_time', 'open': 'open_time',
    'closetime': 'close_time', 'closes': 'close_time', 'closesat': 'close_time',
    'closingtime': 'close_time', 'close': 'close_time',
    'active': 'is_active', 'isactive': 'is_active', 'showinapp': 'is_active',
    'status': 'is_active',
}

TEMPLATE_HEADERS = [
    ('Store Code', 'code'), ('Store Name', 'name'),
    ('City', 'city'), ('State', 'state'),
    ('Latitude', 'lat'), ('Longitude', 'lng'),
    ('Address', 'address'), ('PIN Code', 'pincode'), ('Phone', 'phone'),
    ('Opens At', 'open_time'), ('Closes At', 'close_time'),
    ('Show In App', 'is_active'),
]

LATLNG_RE = re.compile(r'(-?\d{1,2}\.\d+)\s*[, ]\s*(-?\d{1,3}\.\d+)')
MAPS_AT_RE = re.compile(r'@(-?\d+\.\d+),(-?\d+\.\d+)')


def _norm(h):
    return re.sub(r'[^a-z]', '', str(h or '').lower())


def _num(v):
    if v is None or str(v).strip() == '':
        return None
    try:
        return float(str(v).strip())
    except ValueError:
        return None


def _clock(v):
    """Excel stores "9:30 AM" as a real time value, which stringifies as
    '09:30:00'. Shoppers should see 9:30 AM."""
    if v is None or str(v).strip() == '':
        return None
    if isinstance(v, (dt.time, dt.datetime)):
        h, m = v.hour, v.minute
        return f'{(h % 12) or 12}:{m:02d} {"AM" if h < 12 else "PM"}'
    s = str(v).strip()
    m = re.match(r'^(\d{1,2}):(\d{2})(?::\d{2})?$', s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23:
            return f'{(h % 12) or 12}:{mi:02d} {"AM" if h < 12 else "PM"}'
    return s[:10]


def _truthy(v):
    if v is None or str(v).strip() == '':
        return True                      # default: show the store
    return str(v).strip().lower() in ('1', 'y', 'yes', 'true', 'active', 'show', 'on')


# ---------------------------------------------------------------- template
def build_template(stores):
    """An .xlsx of every store with the coordinates we currently hold."""
    wb = Workbook()
    ws = wb.active
    ws.title = 'Store Locations'
    ws.append([h for h, _ in TEMPLATE_HEADERS])
    for c in range(1, len(TEMPLATE_HEADERS) + 1):
        cell = ws.cell(row=1, column=c)
        cell.font = cell.font.copy(bold=True)
    for s in sorted(stores, key=lambda x: (x.get('state') or '', x.get('name') or '')):
        ws.append([
            s.get('code'), s.get('name'), s.get('city') or '', s.get('state') or '',
            s.get('lat'), s.get('lng'),
            s.get('address') or '', s.get('pincode') or '', s.get('phone') or '',
            s.get('open_time') or '', s.get('close_time') or '',
            'Yes' if s.get('is_active', True) else 'No',
        ])
    widths = (12, 28, 18, 18, 12, 12, 52, 10, 16, 12, 12, 12)
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
    ws.freeze_panes = 'A2'

    notes = wb.create_sheet('How to fill this')
    for line in [
        ['V2 Customer App — store list upload'],
        [''],
        ['THIS SHEET IS THE STORE LIST. The app shows these stores and only these'],
        ['stores — nothing is pulled from the warehouse. A store missing from this'],
        ['sheet does not appear in Find My Store.'],
        [''],
        ['1. Store Code must match the V2 store code (HO21, HK12, ...). It is the'],
        ['   key used to match a shopper\'s bills to the store.'],
        ['2. Latitude must be between 6 and 38, Longitude between 68 and 98 (India).'],
        ['   Easiest way to get them: open the store in Google Maps, right-click'],
        ['   the pin, click the coordinates to copy, paste into Latitude.'],
        ['   You can also paste the whole "22.1167, 84.0333" into Latitude and'],
        ['   leave Longitude blank — the upload will split it.'],
        ['3. Store Name, City and State are what the shopper sees. Fill them in —'],
        ['   a store with no name shows as its code.'],
        ['4. Leave a cell blank to keep whatever is already saved for that store.'],
        ['5. Show In App: Yes or No. No hides the store from the store finder'],
        ['   without deleting it.'],
        [''],
        ['The file is previewed before anything is saved — you will see exactly'],
        ['what will change, and what could not be read, before you confirm.'],
        [''],
        ['The first template you download is pre-filled from our store master as'],
        ['a starting point. After that it comes back filled with whatever you'],
        ['last uploaded, so it stays your working copy.'],
    ]:
        notes.append(line)
    notes.column_dimensions['A'].width = 78

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# ---------------------------------------------------------------- parse
CODE_RE = re.compile(r'^[A-Z0-9][A-Z0-9 _-]{1,19}$')


def parse(file_bytes, known_codes=None):
    """-> (rows, report). Never writes anything; this is the preview step.

    `known_codes` is advisory only. The sheet defines the estate, so a code we
    have not seen before is accepted and merely noted — a newly opened store
    must not be rejected just because the warehouse has not caught up.
    """
    try:
        wb = load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
    except Exception as exc:
        raise ValueError(f'That file could not be opened as an Excel workbook ({exc}). '
                         'Save it as .xlsx and try again.')
    ws = wb.active

    rows_iter = ws.iter_rows(values_only=True)
    try:
        header = next(rows_iter)
    except StopIteration:
        raise ValueError('The sheet is empty.')

    mapping, unmapped = {}, []
    for idx, h in enumerate(header):
        if h is None or str(h).strip() == '':
            continue
        key = ALIASES.get(_norm(h))
        if key:
            mapping.setdefault(key, idx)
        else:
            unmapped.append(str(h).strip())
    if 'code' not in mapping:
        raise ValueError('No "Store Code" column found. The sheet needs a column '
                         'headed Store Code (or Code / ST_CD).')

    out, problems, seen, new_codes = [], [], set(), []
    for n, raw in enumerate(rows_iter, start=2):
        if raw is None or all(c is None or str(c).strip() == '' for c in raw):
            continue

        def cell(field):
            i = mapping.get(field)
            return raw[i] if i is not None and i < len(raw) else None

        code = str(cell('code') or '').strip().upper()
        if not code:
            problems.append({'row': n, 'code': '', 'why': 'No store code'})
            continue
        if code in seen:
            problems.append({'row': n, 'code': code, 'why': 'Duplicate row for this store'})
            continue
        if not CODE_RE.match(code):
            problems.append({'row': n, 'code': code[:20],
                             'why': 'That does not look like a store code'})
            continue
        if known_codes and code not in known_codes:
            new_codes.append(code)          # noted in the preview, not rejected
        seen.add(code)

        lat, lng = _num(cell('lat')), _num(cell('lng'))
        if lat is None:                       # "22.1167, 84.0333" or a maps link
            blob = str(cell('lat') or '')
            m = MAPS_AT_RE.search(blob) or LATLNG_RE.search(blob)
            if m:
                lat, lng = float(m.group(1)), float(m.group(2))
        if lat is not None and not (6 <= lat <= 38):
            problems.append({'row': n, 'code': code,
                             'why': f'Latitude {lat} is outside India (6–38)'})
            continue
        if lng is not None and not (68 <= lng <= 98):
            problems.append({'row': n, 'code': code,
                             'why': f'Longitude {lng} is outside India (68–98)'})
            continue
        if (lat is None) != (lng is None):
            problems.append({'row': n, 'code': code,
                             'why': 'Only one of latitude/longitude was given'})
            continue

        def text(field, limit):
            v = cell(field)
            if v is None or str(v).strip() == '':
                return None
            # Excel types a PIN code or phone number as a number, which
            # stringifies as '770001.0' — strip the spurious decimal
            if isinstance(v, float) and v.is_integer():
                v = int(v)
            return str(v).strip()[:limit]

        out.append({
            'code': code,
            'name': text('name', 120), 'city': text('city', 80),
            'state': text('state', 80),
            'lat': lat, 'lng': lng,
            'address': text('address', 500), 'pincode': text('pincode', 10),
            'phone': text('phone', 40),
            'open_time': _clock(cell('open_time')),
            'close_time': _clock(cell('close_time')),
            'is_active': _truthy(cell('is_active')),
        })

    report = {
        'parsed': len(out),
        'problems': problems[:200],
        'problem_count': len(problems),
        'unmapped_columns': unmapped,
        'mapped_columns': sorted(mapping),
        'new_codes': new_codes[:50],
        'new_code_count': len(new_codes),
        'with_geo': sum(1 for r in out if r['lat'] is not None),
        'hidden': sum(1 for r in out if not r['is_active']),
        'sheet': ws.title,
    }
    return out, report


def diff(rows, current):
    """What the upload would actually change, per store."""
    changes = []
    for r in rows:
        cur = current.get(r['code'], {})
        fields = []
        for f in PRESERVE:
            new = r.get(f)
            if new is None:
                continue                     # blank cell = leave as it is
            old = cur.get(f)
            if f in ('lat', 'lng'):
                if old is None or abs(float(old) - float(new)) > 1e-6:
                    fields.append(f)
            elif (old or '') != (new or ''):
                fields.append(f)
        changes.append({
            'code': r['code'],
            'new_store': r['code'] not in current,
            'new_pin': cur.get('lat') is None and r.get('lat') is not None,
            'fields': fields,
        })
    removed = sorted(set(current) - {r['code'] for r in rows})
    return {
        'rows': len(rows),
        'new_stores': sum(1 for c in changes if c['new_store']),
        'new_pins': sum(1 for c in changes if c['new_pin']),
        'changed': sum(1 for c in changes if c['fields']),
        'unchanged': sum(1 for c in changes if not c['fields']),
        # an upload adds and updates; it never silently drops a store you
        # already published. Removing one is an explicit action in the panel.
        'not_in_sheet': removed[:200],
        'not_in_sheet_count': len(removed),
        'detail': changes[:300],
    }


PRESERVE = ('name', 'city', 'state', 'lat', 'lng', 'address', 'pincode',
            'phone', 'open_time', 'close_time')


def commit(rows, by='admin'):
    """Write the parsed rows to Snowflake. Returns (saved, failures).

    A blank cell means "leave this as it is", so each row is merged over what is
    already stored before it is written. Without this, uploading a sheet that
    only fills in addresses would blank out every coordinate on it.
    """
    existing = sf_admin.list_stores()
    saved, failures = 0, []
    for r in rows:
        cur = existing.get(r['code'], {})
        merged = dict(r)
        for f in PRESERVE:
            if merged.get(f) is None and cur.get(f) is not None:
                merged[f] = cur[f]
        try:
            sf_admin.save_location(r['code'], merged, by=by)
            saved += 1
        except Exception as exc:
            failures.append({'code': r['code'], 'why': str(exc)[:200]})
    return saved, failures
