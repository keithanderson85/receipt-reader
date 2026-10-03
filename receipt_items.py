"""Merge line items read from several overlapping photos of one receipt.

When a long receipt is photographed in pieces, neighbouring photos usually
overlap, and sometimes the same photo is taken twice. The vision model reports
every line it can see in each photo (tagged with a 1-based ``photo`` number);
this module recognises the repeated lines by item identity (SKU, or
description + price) and counts each physical line once.
"""
import difflib
import re
from typing import Dict, List, Tuple

DESC_MATCH_RATIO = 0.85
SUBTOTAL_TOLERANCE = 0.02


def _norm_desc(item: Dict) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', str(item.get('description') or '').lower()).strip()


def _norm_sku(item: Dict) -> str:
    return re.sub(r'\s+', '', str(item.get('sku') or '')).lower()


def _price(item: Dict):
    try:
        return round(float(item.get('price')), 2)
    except (TypeError, ValueError):
        return None


def same_item(a: Dict, b: Dict) -> bool:
    """True when two OCR'd lines are almost certainly the same receipt line."""
    if _price(a) != _price(b):
        return False
    sku_a, sku_b = _norm_sku(a), _norm_sku(b)
    if sku_a and sku_b:
        return sku_a == sku_b
    desc_a, desc_b = _norm_desc(a), _norm_desc(b)
    if not desc_a or not desc_b:
        return False
    return desc_a == desc_b or difflib.SequenceMatcher(None, desc_a, desc_b).ratio() >= DESC_MATCH_RATIO


def _contains_run(haystack: List[Dict], needle: List[Dict]) -> bool:
    """True if ``needle`` appears as a contiguous run of same items in ``haystack``."""
    n = len(needle)
    for start in range(len(haystack) - n + 1):
        if all(same_item(haystack[start + i], needle[i]) for i in range(n)):
            return True
    return False


def _overlap_size(prev: List[Dict], cur: List[Dict]) -> int:
    """Largest k such that the last k items of ``prev`` equal the first k of ``cur``."""
    for k in range(min(len(prev), len(cur)), 0, -1):
        if all(same_item(prev[len(prev) - k + i], cur[i]) for i in range(k)):
            return k
    return 0


def merge_photo_items(items: List[Dict], photo_count: int) -> Tuple[List[Dict], List[Dict]]:
    """Collapse items repeated across photos.

    Returns ``(merged_items, notes)`` where each note is
    ``{'level': 'info' | 'warning', 'message': str}``.
    Items without a ``photo`` tag are left untouched.
    """
    if not items or photo_count < 2 or not any(it.get('photo') for it in items):
        return items, []

    by_photo: Dict[int, List[Dict]] = {}
    for it in items:
        try:
            photo = int(it.get('photo') or 1)
        except (TypeError, ValueError):
            photo = 1
        by_photo.setdefault(min(max(photo, 1), photo_count), []).append(it)

    merged: List[Dict] = []
    notes: List[Dict] = []
    seen: Dict[int, List[Dict]] = {}
    prev_photo = None

    for photo in sorted(by_photo):
        cur = by_photo[photo]

        repeat_of = next(
            (p for p, earlier in seen.items() if len(cur) >= 2 and _contains_run(earlier, cur)),
            None,
        )
        if repeat_of is not None:
            notes.append({
                'level': 'warning',
                'message': f'Photo {photo} shows the same {len(cur)} items as photo {repeat_of} '
                           f'- it looks like a repeat, so it was ignored.',
            })
            continue

        keep = cur
        if prev_photo is not None:
            k = _overlap_size(seen[prev_photo], cur)
            if k:
                keep = cur[k:]
                notes.append({
                    'level': 'info',
                    'message': f'Photos {prev_photo} and {photo} overlap by {k} '
                               f'item{"s" if k != 1 else ""} - counted once.',
                })

        merged.extend(keep)
        seen[photo] = cur
        prev_photo = photo

    for it in merged:
        it.pop('photo', None)
    return merged, notes


def subtotal_note(items: List[Dict], subtotal) -> List[Dict]:
    """Warn when the items don't add up to the printed subtotal (missing/doubled section)."""
    try:
        printed = float(subtotal)
    except (TypeError, ValueError):
        return []
    total = 0.0
    for it in items:
        price = _price(it)
        if price is None:
            continue
        try:
            qty = int(it.get('quantity') or 1)
        except (TypeError, ValueError):
            qty = 1
        total += price * qty
    if abs(total - printed) <= SUBTOTAL_TOLERANCE:
        return []
    return [{
        'level': 'warning',
        'message': f'Items add up to ${total:.2f} but the receipt subtotal is ${printed:.2f}. '
                   f'A section may be missing or counted twice - check the photos.',
    }]
