"""Work out which physical store a receipt belongs to.

Receipts name the same store many ways ("Metro Pawn", "METRO PAWN #E0205", "Metro Pawn #" cut off, "EZPWN HE0114",
"EZPAWN #EU101"), so grouping on the raw merchant text splits one store into many. This module:

  * pulls the store number out of the name and repairs OCR typos in it (E203 -> E0203, EU101 -> E0101, EO104 -> E0104)
  * merges spelling variants of one brand (EZPWN / EZ Pawn -> EZPAWN; "Cash America West, Inc." -> Cash America)
  * places a receipt at one of the user's saved locations by its address, its store number, or its saved name
  * otherwise names the store "<Brand> #<number>" (distinct numbered stores stay distinct) or just "<Brand>"

Pure functions and a small index class; no database or Flask in here, so it is easy to test.
"""
import difflib
import re
from collections import defaultdict
from typing import Dict, Iterable, List, Optional

ADDRESS_MATCH_RATIO = 0.8          # same threshold the rest of the app uses for "this is the saved address"
BRAND_MERGE_RATIO = 0.84           # spelling variants of one brand
CORPORATE_SUFFIXES = {'inc', 'llc', 'co', 'corp', 'corporation', 'ltd', 'company', 'incorporated'}

_CODE_HASH = re.compile(r'#\s*([A-Za-z]?)\s*([0-9OoUu]{3,5})\b')
# E0203, HE0114 (a stray leading letter is skipped), EU101, EO104. It needs a real digit, so words like ABOUT never match.
_WORD_CODE = r'\b[A-Z]*([A-Z])((?=[0-9OU]{3,4}\b)[OU]*[0-9][0-9OU]*)\b'
_CODE_WORD = re.compile(_WORD_CODE)
_CODE_ANY = re.compile(r'#\s*[A-Za-z]?\s*[0-9OoUu]{3,5}\b|' + _WORD_CODE.replace('([A-Z])((?', '[A-Z]((?') + r'|#', re.I)


def _text(value) -> str:
    """Text only: NULL, None and pandas NaN all become ''."""
    return value if isinstance(value, str) else ''


def normalize_code(raw: Optional[str]) -> Optional[str]:
    """'e203' / 'E0203' / 'EU101' / '#2028' -> 'E0203' / 'E0203' / 'E0101' / '2028'; None if it isn't a store number."""
    text = _text(raw).strip().upper().lstrip('#').strip()
    m = re.fullmatch(r'([A-Z]?)\s*([0-9OU]{3,5})', text)
    if not m:
        return None
    letter, digits = m.group(1), m.group(2).replace('O', '0').replace('U', '0')
    if not digits.strip('0'):
        return None
    return letter + digits.zfill(4) if letter else digits


def extract_code(name: Optional[str]) -> Optional[str]:
    """The store number printed in a merchant name, repaired; None when the name carries none."""
    name = _text(name)
    if not name:
        return None
    m = _CODE_HASH.search(name)
    if m:
        return normalize_code(m.group(1) + m.group(2))
    m = _CODE_WORD.search(name.upper())
    return normalize_code(m.group(1) + m.group(2)) if m else None


def base_name(name: Optional[str]) -> str:
    """Lower-case words of the name without the store number, punctuation or corporate suffixes."""
    text = _CODE_ANY.sub(' ', _text(name))
    words = re.sub(r"[^a-z0-9 ]", ' ', text.lower().replace("'", '')).split()
    while words and words[-1] in CORPORATE_SUFFIXES:
        words.pop()
    return ' '.join(words)


def _in_order(short: List[str], long: List[str]) -> bool:
    it = iter(long)
    return all(word in it for word in short)


def _similar(a: str, b: str) -> bool:
    """Is this the same business written differently?

    Two cases: an OCR/spelling variant (same first letter, within a couple of characters, very similar), or a
    shorter name whose words all appear in order in a longer one ("St Vincents Thrift" / "St Vincents Super Thrift").
    Names carrying digits never merge (Store 01 and Store 02 are different stores), and similar-looking but
    different names (FirstDay... / LastDay...) stay apart because the first letter differs.
    """
    if any(ch.isdigit() for ch in a + b) or not a or not b or a[0] != b[0]:
        return False
    if abs(len(a) - len(b)) <= 2 and difflib.SequenceMatcher(None, a, b).ratio() >= BRAND_MERGE_RATIO:
        return True
    wa, wb = a.split(), b.split()
    short, long = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    return len(short) >= 2 and short[0] == long[0] and _in_order(short, long)


class StoreIndex:
    """Resolves receipts to stores. Build it once per request from the user's locations and all receipt names."""

    def __init__(self, locations: Iterable[Dict], receipts: Iterable[Dict]):
        """locations: dicts with id, name, address, store_codes; receipts: dicts with merchant_name, amount."""
        self.locations: List[Dict] = [dict(l) for l in locations]
        weights: Dict[str, float] = defaultdict(float)
        spellings: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for r in receipts:
            b = base_name(r.get('merchant_name'))
            if b:
                weight = abs(r.get('amount') or 0) + 0.01
                weights[b] += weight
                spellings[b][_text(r.get('merchant_name')).split('#')[0].strip()] += weight
        for loc in self.locations:
            b = base_name(loc.get('name'))
            if b:
                weights[b] += 0.005
                spellings[b][loc['name']] += 0.005

        # brand heads: shortest names first (so "metro pawn" absorbs "metro pawn keystone"), heaviest first within a length
        self._head: Dict[str, str] = {}
        heads: List[str] = []
        for b in sorted(weights, key=lambda x: (len(x.split()), -weights[x])):
            home = next((h for h in heads if b == h or b.startswith(h + ' ') or _similar(b, h)), None)
            if home is None:
                heads.append(b)
                home = b
            self._head[b] = home
        # the brand's display name comes from names whose base IS the brand ("Metro Pawn"), never from a longer
        # variant that merely rolls up into it ("Metro Pawn Keystone" must not rename the brand)
        # Spellings that are merely the brand plus a place ("metro pawn keystone") never name it, but a similar
        # spelling of the same business does ("St Vincents Thrift" is mostly written "St Vincent's Super Thrift").
        self._display: Dict[str, str] = {}
        for h in heads:
            pool: Dict[str, float] = defaultdict(float)
            for b, head in self._head.items():
                if head == h and (b == h or not b.startswith(h + ' ')):
                    for text, w in spellings[b].items():
                        pool[text] += w
            self._display[h] = self._clean_display(max(pool, key=pool.get))

        self._loc_by_code: Dict[str, Dict] = {}
        self._loc_by_name: Dict[str, Dict] = {}
        self._brands_with_locations = set()
        for loc in self.locations:
            for code in re.split(r'[,\s]+', loc.get('store_codes') or ''):
                code = normalize_code(code)
                if code:
                    self._loc_by_code[code] = loc
            self._loc_by_name[base_name(loc.get('name'))] = loc
            self._brands_with_locations.add(self.brand_key(loc.get('name')))

    @staticmethod
    def _clean_display(text: str) -> str:
        text = re.sub(r'[\s,]+(inc|llc|co|corp|ltd)\.?$', '', text.strip(), flags=re.I).strip(' ,')
        return text

    def brand_key(self, name: Optional[str]) -> str:
        b = base_name(name)
        return self._head.get(b) or b

    def brand_label(self, name: Optional[str]) -> str:
        key = self.brand_key(name)
        return self._display.get(key) or _text(name).strip() or 'Unknown'

    def location_for_address(self, address: Optional[str]) -> Optional[Dict]:
        text = _text(address).lower().strip()
        if not text:
            return None
        best, best_ratio = None, 0.0
        for loc in self.locations:
            target = _text(loc.get('address')).lower().strip()
            if not target:
                continue
            ratio = difflib.SequenceMatcher(None, text, target).ratio()
            if ratio > ADDRESS_MATCH_RATIO and ratio > best_ratio:
                best, best_ratio = loc, ratio
        return best

    def resolve(self, merchant_name: Optional[str], address: Optional[str] = None) -> Dict:
        """-> {'store', 'brand', 'location_id', 'code', 'placed'}  (placed: tied to a saved location or a store number)."""
        code = extract_code(merchant_name)
        loc = (self.location_for_address(address)
               or (self._loc_by_code.get(code) if code else None)
               or self._loc_by_name.get(base_name(merchant_name)))
        if loc:
            return {'store': loc['name'], 'brand': self.brand_label(loc['name']), 'location_id': loc['id'],
                    'code': code, 'placed': True}
        brand = self.brand_label(merchant_name)
        if code:
            return {'store': f'{brand} #{code}', 'brand': brand, 'location_id': None, 'code': code, 'placed': True}
        if self.brand_key(merchant_name) in self._brands_with_locations:
            return {'store': f'{brand} (no location)', 'brand': brand, 'location_id': None, 'code': None,
                    'placed': False}
        return {'store': brand, 'brand': brand, 'location_id': None, 'code': None, 'placed': True}

    def locations_of_brand(self, brand_key: str) -> List[Dict]:
        return [l for l in self.locations if self.brand_key(l.get('name')) == brand_key]

    def unassigned_codes(self, receipts: Iterable[Dict]):
        """Store numbers seen on receipts whose brand has saved locations but that no location claims yet."""
        found: Dict[str, Dict] = {}
        for r in receipts:
            code = extract_code(r.get('merchant_name'))
            if not code or code in self._loc_by_code or self.location_for_address(r.get('address')):
                continue
            key = self.brand_key(r.get('merchant_name'))
            if key not in self._brands_with_locations or base_name(r.get('merchant_name')) in self._loc_by_name:
                continue
            entry = found.setdefault(code + '|' + key, {'code': code, 'brand': self.brand_label(r.get('merchant_name')),
                                                         'brand_key': key, 'count': 0, 'total': 0.0})
            entry['count'] += 1
            entry['total'] = round(entry['total'] + (r.get('amount') or 0), 2)
        return sorted(found.values(), key=lambda e: -e['total'])
