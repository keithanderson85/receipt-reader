"""Working out which physical store a receipt belongs to (pure logic, no Flask)."""
import time

import pytest

from store_resolver import StoreIndex, base_name, extract_code, normalize_code

KEYSTONE = {'id': 1, 'name': 'Metro Pawn Keystone', 'address': '800 W. 5th St NV 89503', 'store_codes': ''}
ODDIE = {'id': 2, 'name': 'Metro Pawn Oddie', 'address': '2625 Oddie Blvd NV 89512', 'store_codes': 'E0203'}


def rec(name, amount=10.0, address=None):
    return {'merchant_name': name, 'amount': amount, 'address': address}


# ---- store numbers ----------------------------------------------------------------------------------

@pytest.mark.parametrize('raw, expected', [
    ('E0203', 'E0203'), ('e203', 'E0203'), ('#E203', 'E0203'), ('EU101', 'E0101'), ('EO104', 'E0104'),
    ('#2028', '2028'), ('2028', '2028'), ('E0120', 'E0120'),
    ('', None), (None, None), ('junk', None), ('E', None), ('000', None), ('12', None),
])
def test_normalize_code(raw, expected):
    assert normalize_code(raw) == expected


@pytest.mark.parametrize('name, expected', [
    ('Metro Pawn #E0205', 'E0205'), ('Metro Pawn #E203', 'E0203'),               # missing zero
    ('EZPAWN #EU101', 'E0101'), ('EZPAWN #EO104', 'E0104'),                      # O/U read for 0
    ('EZPWN HE0114', 'E0114'), ('Ezpwn He0114', 'E0114'),                        # stray leading letter, any case
    ('Cash America #2028', '2028'), ('COSTCO #123', '123'),
    ('Metro Pawn #', None), ('Metro Pawn', None), ('Amazon.com', None), ('IKEA', None),
    ('ABOUT TIME', None), ('SOUL FOOD', None), ('BOOU', None),                    # words are not codes
    ('Store 01', None), ('Target T-1234', None), (None, None), ('', None),
])
def test_extract_code(name, expected):
    assert extract_code(name) == expected


def test_base_name_drops_codes_punctuation_and_suffixes():
    assert base_name('Cash America West, Inc.') == 'cash america west'
    assert base_name("St. Vincent's Super Thrift") == 'st vincents super thrift'
    assert base_name('EZPAWN #E0104') == 'ezpawn'
    assert base_name('Ezpwn He0114') == 'ezpwn'
    assert base_name(None) == ''


# ---- brands -----------------------------------------------------------------------------------------

def brands(*names):
    idx = StoreIndex([], [rec(n) for n in names])
    return {n: idx.brand_label(n) for n in names}


def test_a_location_suffix_rolls_up_into_the_brand():
    got = brands('Metro Pawn', 'Metro Pawn Keystone', 'Metro Pawn Glendale', 'METRO PAWN #E0205')
    assert len(set(got.values())) == 1


def test_ocr_typos_and_spellings_merge():
    got = brands('EZPAWN', 'EZPWN HE0114', 'EZ Pawn', 'EZPAWN #EU101')
    assert len(set(got.values())) == 1
    assert len(set(brands('Home Depot', 'Home Deport', 'THE HOME DEPOT').values())) <= 2           # typo merges


def test_shorter_name_whose_words_appear_in_the_longer_merges():
    got = brands("St. Vincent's Super Thrift", 'St. Vincents Thrift')
    assert len(set(got.values())) == 1


def test_similar_looking_but_different_businesses_stay_apart():
    assert len(set(brands('FirstDayOfLastYear', 'LastDayOfLastYear').values())) == 2
    assert len(set(brands('Store 01', 'Store 02', 'Store 03').values())) == 3                      # digits never merge
    assert len(set(brands('Dollar Tree', 'Dollar General').values())) == 2
    assert len(set(brands('Shell', 'Staples', 'Starbucks').values())) == 3


def test_brand_display_uses_the_spelling_with_the_most_spend():
    idx = StoreIndex([], [rec('Metro Pawn', 900), rec('METRO PAWN', 50), rec('Metro Pawn #E0205', 40)])
    assert idx.brand_label('metro pawn') == 'Metro Pawn'


def test_a_brand_is_named_by_its_own_receipts_not_by_a_busy_sub_store():
    receipts = [rec('Metro Pawn', 10), rec('Metro Pawn Keystone', 99999), rec('Metro Pawn Glendale', 88888)]
    assert StoreIndex([], receipts).brand_label('Metro Pawn Keystone') == 'Metro Pawn'


def test_a_brand_takes_the_name_of_its_most_used_similar_spelling():
    receipts = [rec("St. Vincent's Super Thrift", 1364), rec('St. Vincents Thrift', 230)]
    idx = StoreIndex([], receipts)
    assert idx.brand_label('St. Vincents Thrift') == "St. Vincent's Super Thrift"


# ---- placing a receipt at a store --------------------------------------------------------------------

def test_address_places_a_receipt_at_the_saved_location():
    idx = StoreIndex([KEYSTONE, ODDIE], [rec('Metro Pawn')])
    r = idx.resolve('Metro Pawn', '800 W. 5th St NV 89503')
    assert r['store'] == 'Metro Pawn Keystone' and r['location_id'] == 1 and r['placed'] and r['brand'] == 'Metro Pawn'


def test_address_match_tolerates_small_ocr_differences_but_not_other_streets():
    idx = StoreIndex([KEYSTONE], [])
    assert idx.resolve('x', '800 W 5th St, NV 89503')['location_id'] == 1
    assert idx.resolve('x', '1234 Totally Different Rd, Reno NV')['location_id'] is None


def test_store_number_places_a_receipt_with_no_address():
    idx = StoreIndex([KEYSTONE, ODDIE], [rec('Metro Pawn #E0203')])
    assert idx.resolve('Metro Pawn #E0203')['store'] == 'Metro Pawn Oddie'
    assert idx.resolve('Metro Pawn #E203')['store'] == 'Metro Pawn Oddie'                              # typo still finds it


def test_address_beats_store_number():
    idx = StoreIndex([KEYSTONE, ODDIE], [])
    assert idx.resolve('Metro Pawn #E0203', '800 W. 5th St NV 89503')['store'] == 'Metro Pawn Keystone'


def test_saved_name_places_a_receipt():
    idx = StoreIndex([KEYSTONE], [])
    assert idx.resolve('METRO PAWN KEYSTONE')['location_id'] == 1


def test_unnumbered_receipts_of_a_brand_with_saved_locations_are_honestly_unplaced():
    idx = StoreIndex([KEYSTONE, ODDIE], [rec('Metro Pawn')])
    r = idx.resolve('Metro Pawn')
    assert r['store'] == 'Metro Pawn (no location)' and not r['placed'] and r['brand'] == 'Metro Pawn'


def test_numbered_stores_of_an_unsaved_brand_stay_distinct_and_typos_collapse():
    names = ['EZPAWN #E0101', 'EZPAWN #EU101', 'EZPAWN #E0104', 'EZPAWN #EO104', 'EZPWN HE0114', 'EZPAWN']
    idx = StoreIndex([], [rec(n) for n in names])
    stores = [idx.resolve(n)['store'] for n in names]
    assert stores == ['EZPAWN #E0101', 'EZPAWN #E0101', 'EZPAWN #E0104', 'EZPAWN #E0104', 'EZPAWN #E0114', 'EZPAWN']


def test_plain_brand_without_saved_locations_is_just_the_brand():
    idx = StoreIndex([KEYSTONE], [rec('Home Depot')])
    r = idx.resolve('Home Depot')
    assert r['store'] == 'Home Depot' and r['placed']


def test_codes_are_per_user_data_not_global():
    mine = StoreIndex([dict(KEYSTONE, store_codes='E0205')], [])
    theirs = StoreIndex([], [])
    assert mine.resolve('Metro Pawn #E0205')['location_id'] == 1
    assert theirs.resolve('Metro Pawn #E0205')['location_id'] is None


def test_blank_and_missing_names_do_not_crash():
    idx = StoreIndex([KEYSTONE], [rec(None), rec('')])
    for name in (None, '', '   ', '#', '###'):
        assert idx.resolve(name, None)['store']


# ---- store numbers that still need assigning ---------------------------------------------------------------

def test_unassigned_codes_lists_numbers_of_brands_that_have_saved_locations():
    receipts = [rec('Metro Pawn #E0205', 100), rec('Metro Pawn #E0205', 50), rec('Metro Pawn #E0203', 10),
                rec('Metro Pawn #E0206', 500), rec('EZPAWN #E0101', 999)]          # EZPAWN has no saved locations
    idx = StoreIndex([KEYSTONE, ODDIE], receipts)
    got = idx.unassigned_codes(receipts)
    assert [(g['code'], g['count'], g['total']) for g in got] == [('E0206', 1, 500.0), ('E0205', 2, 150.0)]   # E0203 is claimed
    assert all(g['brand'] == 'Metro Pawn' for g in got)


def test_assigning_a_code_removes_it_from_the_list_and_places_the_receipts():
    receipts = [rec('Metro Pawn #E0205', 100)]
    before = StoreIndex([KEYSTONE, ODDIE], receipts)
    assert [g['code'] for g in before.unassigned_codes(receipts)] == ['E0205']
    after = StoreIndex([dict(KEYSTONE, store_codes='E0205'), ODDIE], receipts)
    assert after.unassigned_codes(receipts) == [] and after.resolve('Metro Pawn #E0205')['store'] == 'Metro Pawn Keystone'


def test_receipts_with_a_matching_address_are_not_listed_as_unassigned():
    receipts = [rec('Metro Pawn #E0205', 100, '800 W. 5th St NV 89503')]
    assert StoreIndex([KEYSTONE], receipts).unassigned_codes(receipts) == []


def test_resolving_thousands_of_receipts_is_fast_enough_for_a_page_load():
    receipts = [rec(f'Metro Pawn #E{n % 20:04d}', 5.0, '800 W. 5th St NV 89503' if n % 3 == 0 else None) for n in range(3000)]
    idx = StoreIndex([KEYSTONE, ODDIE], receipts)
    started = time.time()
    for r in receipts:
        idx.resolve(r['merchant_name'], r['address'])
    assert time.time() - started < 3.0
