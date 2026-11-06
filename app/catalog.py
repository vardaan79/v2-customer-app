"""
catalog.py — turn V2's internal article codes into customer-readable product names.

The POS line carries an 18-char SAP material number, e.g. 000001130117973001.
Strip leading zeros -> 1130117973001 ; the first 10 digits are the GEN ART
(1130117973) and the trailing 3 are the size/variant. Non-apparel lines come
through 10 digits already (000000001233050741 -> 1233050741).

Descriptions come from (in priority order):
  SILVER.DIM_ARTICLE_V2.MATERIAL_GROUP_DESC   e.g. 'L_KURTI_HS'  <- best label source
  CDP_WORK.TBL_ARTICLE.MAJOR_CATEGORY         e.g. 'L_KURTI_HS'
  CDP_WORK.ARTMASTER_EAN.PRODUCT              e.g. 'CO_CARRY_BAG-PAPER'
  SILVER.DIM_ARTICLE_V2.ARTICLE_DESC          e.g. 'P-IMP-PRT-RN-C_SLV-...' <- spec
                                                   string, a sub-line, not a title.

Nothing in the warehouse holds a shopper-friendly product name, so this module
decodes the merchandising shorthand into one:

    L_KURTI_HS      -> Ladies Kurti · Half Sleeve
    B-M_TEES_PN_HS  -> Men's T-Shirt · Polo Neck Half Sleeve
    IBW_B_SUIT      -> Infant Boys Baby Suit

Codes are audience-prefix + product noun + modifiers, with fabric/construction
letters (W woven, K knit, T, H, SF) mixed in that mean nothing to a shopper.
Unknown tokens degrade to Title Case rather than vanishing, so a newly created
article never renders as a blank line on someone's bill.
"""
import re

# ---------------------------------------------------------------- audience
AUDIENCE = {
    'M': "Men's", 'MW': "Men's", 'MS': "Men's", 'MEN': "Men's", 'MENS': "Men's",
    'L': 'Ladies', 'LW': 'Ladies', 'LS': 'Ladies', 'W': 'Ladies',
    'LADIES': 'Ladies', 'WOMEN': 'Ladies',
    'JB': 'Boys', 'JBW': 'Boys', 'YB': 'Boys', 'YBW': 'Boys',
    'B': 'Boys', 'BW': 'Boys', 'BOYS': 'Boys',
    'JG': 'Girls', 'JGW': 'Girls', 'YG': 'Girls', 'YGW': 'Girls',
    'G': 'Girls', 'GW': 'Girls', 'GIRLS': 'Girls',
    'IB': 'Infant Boys', 'IBW': 'Infant Boys',
    'IG': 'Infant Girls', 'IGW': 'Infant Girls',
    'IW': 'Infants', 'K': 'Kids', 'KW': 'Kids', 'KIDS': 'Kids',
    'U': 'Unisex', 'UNISEX': 'Unisex',
    'HH': 'Home', 'HW': 'Home', 'HOME': 'Home',
    'MKT': '', 'CO': '', 'NT': '', 'GM': '', 'FW': '',
}

CATEGORY_OF_AUDIENCE = {
    "Men's": "Men's Wear", 'Ladies': 'Ladies Wear',
    'Boys': 'Kids Wear', 'Girls': 'Kids Wear', 'Kids': 'Kids Wear',
    'Infant Boys': 'Kids Wear', 'Infant Girls': 'Kids Wear', 'Infants': 'Kids Wear',
    'Home': 'Home & Living', 'Unisex': '',
}

DIVISION_LABEL = {
    'MENS': "Men's Wear", 'LADIES': 'Ladies Wear', 'KIDS': 'Kids Wear',
    'MENS-KIDS': 'Kids Wear', 'LADIES-KIDS': 'Kids Wear', 'BOYS': 'Kids Wear',
    'GIRLS': 'Kids Wear', 'FOOTWEAR': 'Footwear', 'ACCESSORIES': 'Accessories',
    'HOME': 'Home & Living', 'GM': 'Home & Living',
}

# ---------------------------------------------------------------- product nouns
# two-token phrases are matched before single tokens
COMPOUND = {
    'T_SHIRT': 'T-Shirt', 'CROP_TOP': 'Crop Top', 'HOT_PANT': 'Hot Pants',
    'B_SUIT': 'Baby Suit', 'NIGHT_SUIT': 'Night Suit', 'TRACK_SUIT': 'Tracksuit',
    'CARRY_BAG': 'Carry Bag', 'LUNCH_BOX': 'Lunch Box', 'LOY_CARD': 'V2 VIP Membership Card',
    'SPRT_BRA': 'Sports Bra', 'DRESS_MTRL': 'Dress Material', 'SAREE_BLOUSE': 'Saree Blouse',
}

PRODUCT = {
    'TEES': 'T-Shirt', 'TEE': 'T-Shirt', 'TSHIRT': 'T-Shirt', 'TSHRT': 'T-Shirt',
    'SHIRT': 'Shirt', 'SHIRTS': 'Shirt', 'SHRT': 'Shirt',
    'KURTA': 'Kurta', 'KURTI': 'Kurti', 'KURTIS': 'Kurti', 'KURTHA': 'Kurta',
    'LEGGING': 'Leggings', 'LEGGINGS': 'Leggings', 'JEGGING': 'Jeggings',
    'DUPATTA': 'Dupatta', 'SAREE': 'Saree', 'BLOUSE': 'Blouse', 'LEHENGA': 'Lehenga',
    'PYJAMA': 'Pyjama', 'PAJAMA': 'Pyjama', 'NIGHTY': 'Nighty', 'NIGHTWEAR': 'Nightwear',
    'TRSR': 'Trousers', 'TROUSER': 'Trousers', 'PANT': 'Trousers', 'PANTS': 'Trousers',
    'JEANS': 'Jeans', 'DENIM': 'Denim', 'CHINO': 'Chinos', 'BERMUDA': 'Bermudas',
    'SHORT': 'Shorts', 'SHORTS': 'Shorts', 'CAPRI': 'Capri', 'TIGHTS': 'Tights',
    'PLAZO': 'Palazzo', 'PALAZZO': 'Palazzo', 'PLZO': 'Palazzo',
    'SALWAR': 'Salwar', 'PATIALA': 'Patiala', 'CHURIDAR': 'Churidar',
    'SUIT': 'Suit Set', 'FROCK': 'Frock', 'FRK': 'Frock', 'DRESS': 'Dress',
    'GOWN': 'Gown', 'SKIRT': 'Skirt', 'TOP': 'Top', 'TUNIC': 'Tunic',
    'JKT': 'Jacket', 'JACKET': 'Jacket', 'BLAZER': 'Blazer', 'COAT': 'Coat',
    'SWTR': 'Sweater', 'SWEATER': 'Sweater', 'SWT': 'Sweatshirt',
    'SWTSHRT': 'Sweatshirt', 'HOODIE': 'Hoodie', 'THERMAL': 'Thermal Wear',
    'TRACK': 'Track Pants', 'JOGGER': 'Joggers', 'LOWER': 'Lowers',
    'INNER': 'Innerwear', 'BRA': 'Lingerie', 'PANTY': 'Innerwear',
    'BRIEF': 'Briefs', 'TRUNK': 'Trunks', 'VEST': 'Vest', 'SOCKS': 'Socks',
    'HANKY': 'Handkerchief', 'BELT': 'Belt', 'WALLET': 'Wallet', 'CAP': 'Cap',
    'BAG': 'Bag', 'SCARF': 'Scarf', 'STOLE': 'Stole', 'TIE': 'Tie',
    'FOOTWEAR': 'Footwear', 'SANDAL': 'Sandals', 'SLIPPER': 'Slippers',
    'SHOE': 'Shoes', 'SHOES': 'Shoes', 'CHAPPAL': 'Slippers',
    'FLASK': 'Flask', 'BOTTLE': 'Bottle', 'TOWEL': 'Towel',
    'BEDSHEET': 'Bedsheet', 'BLANKET': 'Blanket', 'CUSHION': 'Cushion',
    'MIX': 'Assorted Apparel', 'CARD': 'V2 VIP Membership Card',
}

# ---------------------------------------------------------------- modifiers
MODIFIER = {
    'HS': 'Half Sleeve', 'FS': 'Full Sleeve', 'SL': 'Sleeveless',
    'PN': 'Polo Neck', 'RN': 'Round Neck', 'VN': 'V-Neck', 'CLR': 'Collared',
    'ST': 'Set', 'SPRT': 'Sports', 'CROP': 'Crop', 'DYED': 'Dyed',
    'BLU': 'Blue', 'BLK': 'Black', 'WHT': 'White',
    'PRT': 'Printed', 'PLN': 'Plain', 'SLD': 'Solid', 'CHK': 'Checked',
    'STP': 'Striped', 'EMB': 'Embroidered', 'WSH': 'Washed', 'BSC': 'Basic',
    'FNC': 'Fancy', 'RGL': 'Regular', 'SLM': 'Slim', 'F': 'Formal', 'C': 'Casual',
    'CASUAL': 'Casual', 'FORMAL': 'Formal', 'SPORTS': 'Sports', 'PARTY': 'Party',
    'COLORS': 'Assorted Colours', 'COLOR': 'Coloured',
}

# fabric / construction letters that mean nothing to a shopper
NOISE = {'W', 'K', 'T', 'H', 'S', 'D', 'N', 'X', 'SF', 'P', 'PC', 'CO', 'AOP', 'V2'}

# whole-code shortcuts for the highest-volume non-merchandise lines
EXACT = {
    'MKT_ST_LOY_CARD': 'V2 VIP Membership Card',
    'CO_CARRY_BAG-N_WVN': 'Carry Bag',
    'CO_CARRY_BAG-PAPER': 'Paper Carry Bag',
    'CO_CARRY_BAG': 'Carry Bag',
    'MKT_GI_UTILITY BAG': 'V2 Utility Bag (Gift)',
    'MKT_SS_FLASK': 'Steel Flask (Gift)',
    'LADIES_MIX': 'Ladies Assorted Apparel',
    'KIDS_MIX': 'Kids Assorted Apparel',
    'MENS_MIX': "Men's Assorted Apparel",
}

CARRY_BAG_RE = re.compile(r'CARRY[_ ]?BAG', re.I)
BRAND_PREFIX_RE = re.compile(r'^[A-Z]-')         # 'B-M_TEES_HS' -> brand marker
SPLIT_RE = re.compile(r'[_\-/ ]+')

ACCESSORY_WORDS = {'Belt', 'Wallet', 'Cap', 'Socks', 'Handkerchief', 'Bag',
                   'Carry Bag', 'Scarf', 'Stole', 'Tie'}
FOOTWEAR_WORDS = {'Footwear', 'Sandals', 'Slippers', 'Shoes'}
HOME_WORDS = {'Towel', 'Bedsheet', 'Blanket', 'Cushion', 'Lunch Box',
              'Bottle', 'Flask'}


def gen_art(itemid):
    """18-char POS material number -> 10-digit gen-art used by the article masters."""
    s = str(itemid or '').strip().lstrip('0')
    if not s:
        return ''
    return s[:10] if len(s) > 10 else s


def variant(itemid):
    """Trailing size/variant code, '' when the line carries none."""
    s = str(itemid or '').strip().lstrip('0')
    return s[10:] if len(s) > 10 else ''


def full_code(itemid):
    """The article code as it is printed on a V2 bill: leading zeros stripped,
    gen art plus the size/colour suffix (e.g. 1112099513013). This is the level
    DIM_ARTICLE_V2 holds COLOUR and SIZE at."""
    return str(itemid or '').strip().lstrip('0')


def parse(code):
    """'L_H_CROP_TOP_HS' -> {'audience': 'Ladies', 'noun': 'Crop Top',
                             'mods': ['Half Sleeve'], 'exact': None}"""
    raw = str(code or '').strip()
    if not raw:
        return {'audience': '', 'noun': '', 'mods': [], 'exact': None}
    if raw.upper() in EXACT:
        return {'audience': '', 'noun': '', 'mods': [], 'exact': EXACT[raw.upper()]}
    if CARRY_BAG_RE.search(raw):
        return {'audience': '', 'noun': 'Carry Bag', 'mods': [], 'exact':
                'Paper Carry Bag' if 'PAPER' in raw.upper() else 'Carry Bag'}

    body = BRAND_PREFIX_RE.sub('', raw.upper())
    parts = [p for p in SPLIT_RE.split(body) if p]
    if not parts:
        return {'audience': '', 'noun': '', 'mods': [], 'exact': raw.title()}

    audience = ''
    if parts[0] in AUDIENCE:
        audience = AUDIENCE[parts[0]]
        parts = parts[1:]

    noun, mods, i = '', [], 0
    while i < len(parts):
        p = parts[i]
        pair = f'{p}_{parts[i + 1]}' if i + 1 < len(parts) else None
        if pair and pair in COMPOUND:
            if not noun:
                noun = COMPOUND[pair]
            else:
                mods.append(COMPOUND[pair])
            i += 2
            continue
        if not noun and p in PRODUCT:
            noun = PRODUCT[p]
        elif p in MODIFIER:
            mods.append(MODIFIER[p])
        elif p in NOISE:
            pass
        elif p in PRODUCT:
            mods.append(PRODUCT[p])
        elif not noun and p not in AUDIENCE:
            noun = p.title()
        i += 1

    return {'audience': audience, 'noun': noun, 'mods': mods, 'exact': None}


def product_name(material_group=None, major_category=None, product=None, fallback=None):
    """Best customer-facing title we can build for one line."""
    for src in (material_group, major_category, product):
        if not src or not str(src).strip():
            continue
        p = parse(src)
        if p['exact']:
            return p['exact']
        noun = p['noun'] or 'Apparel'
        if len(noun) < 4 and noun not in PRODUCT.values():
            # an unrecognised trims/packaging code — show it whole rather than
            # pretending the first fragment is a product name
            return str(src).replace('_', ' ').title()
        title = f"{p['audience']} {noun}".strip()
        mods = [m for m in p['mods'] if m and m.lower() not in title.lower()]
        return f'{title} · {" ".join(mods[:3])}' if mods else title
    return (str(fallback).title() if fallback else 'V2 Merchandise')


def category_of(material_group=None, division=None, major_category=None):
    """Bucket a line for the Insights tab."""
    for src in (material_group, major_category):
        code = str(src or '').strip()
        if not code:
            continue
        if code.upper().startswith(('CO_', 'MKT', 'NT_')) or CARRY_BAG_RE.search(code):
            return 'Other'
        p = parse(code)
        noun = p['noun']
        if noun in FOOTWEAR_WORDS:
            return 'Footwear'
        if noun in HOME_WORDS:
            return 'Home & Living'
        if noun in ACCESSORY_WORDS:
            return 'Accessories'
        bucket = CATEGORY_OF_AUDIENCE.get(p['audience'], '')
        if bucket:
            return bucket
    if division:
        d = str(division).strip().upper()
        if d in DIVISION_LABEL:
            return DIVISION_LABEL[d]
    return 'Other'


def spec_line(article_desc):
    """The raw merchandising spec, lightly cleaned, shown as a sub-line on the bill."""
    s = str(article_desc or '').strip()
    if not s or len(s) < 4:
        return ''
    s = s.replace('_', ' ').replace('*', ' x ')
    return ' · '.join(p.strip().title() for p in s.split('-') if p.strip())[:90]
