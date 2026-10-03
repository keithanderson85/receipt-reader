"""Pure logic: merging line items read from overlapping photos of one receipt."""
from conftest import item
from receipt_items import merge_photo_items, same_item, subtotal_note


def names(items):
    return [i['description'] for i in items]


def test_overlap_between_neighbouring_photos_is_counted_once():
    items = [item('MILK', 3.5, 1), item('EGGS', 4, 1), item('BREAD', 2, 1),
             item('Eggs', 4, 2), item('BREAD', 2, 2), item('APPLE', 1, 2)]
    merged, notes = merge_photo_items(items, 2)
    assert names(merged) == ['MILK', 'EGGS', 'BREAD', 'APPLE']
    assert [n['level'] for n in notes] == ['info']
    assert 'overlap by 2 items' in notes[0]['message']


def test_same_photo_twice_is_dropped_with_warning():
    items = [item('A', 1, 1), item('B', 2, 1), item('A', 1, 2), item('B', 2, 2)]
    merged, notes = merge_photo_items(items, 2)
    assert names(merged) == ['A', 'B']
    assert notes[0]['level'] == 'warning' and 'Photo 2' in notes[0]['message']


def test_repeat_of_a_non_adjacent_photo_is_detected():
    items = [item('A', 1, 1), item('B', 2, 1), item('C', 3, 2), item('D', 4, 2), item('A', 1, 3), item('B', 2, 3)]
    merged, notes = merge_photo_items(items, 3)
    assert names(merged) == ['A', 'B', 'C', 'D']
    assert 'Photo 3' in notes[0]['message'] and 'photo 1' in notes[0]['message']


def test_genuine_repeated_lines_inside_one_photo_are_kept():
    items = [item('COKE', 1, 1), item('COKE', 1, 1), item('CHIPS', 2, 2), item('GUM', 1, 2)]
    merged, notes = merge_photo_items(items, 2)
    assert names(merged) == ['COKE', 'COKE', 'CHIPS', 'GUM']
    assert notes == []


def test_single_item_photo_matching_previous_tail_is_overlap_not_repeat():
    items = [item('A', 1, 1), item('B', 2, 1), item('B', 2, 2)]
    merged, notes = merge_photo_items(items, 2)
    assert names(merged) == ['A', 'B']
    assert notes[0]['level'] == 'info'


def test_photo_tag_is_stripped_from_result():
    merged, _ = merge_photo_items([item('A', 1, 1), item('B', 2, 2)], 2)
    assert all('photo' not in i for i in merged)


def test_untagged_items_and_single_photo_are_untouched():
    items = [item('A', 1), item('A', 1)]
    assert merge_photo_items(items, 3) == (items, [])
    assert merge_photo_items([item('A', 1, 1)], 1)[1] == []
    assert merge_photo_items([], 2) == ([], [])


def test_out_of_range_photo_numbers_are_clamped():
    merged, _ = merge_photo_items([item('A', 1, 0), item('B', 2, 9)], 2)
    assert names(merged) == ['A', 'B']


def test_same_item_rules():
    assert same_item(item('MILK 2%', 3.5), item('milk 2%', 3.5))
    assert same_item(item('GV WHL MILK GAL', 3.5), item('GV WHL MILK GAL.', 3.5))   # OCR wobble
    assert not same_item(item('MILK', 3.5), item('MILK', 3.99))                    # price differs
    assert not same_item(item('MILK', 3.5), item('BREAD', 3.5))
    assert same_item(item('X', 1, sku='00123'), item('Y', 1, sku='001 23'))        # sku wins over text
    assert not same_item(item('X', 1, sku='1'), item('X', 1, sku='2'))
    assert not same_item(item('', 1), item('', 1))                                 # nothing to compare


def test_subtotal_note():
    items = [item('A', 2.5, qty=2), item('B', 5)]
    assert subtotal_note(items, 10) == []
    assert subtotal_note(items, 10.01) == []                                       # within tolerance
    warn = subtotal_note(items, 25)
    assert warn and warn[0]['level'] == 'warning' and '$10.00' in warn[0]['message']
    assert subtotal_note(items, None) == []
    assert subtotal_note(items, 'n/a') == []
    assert subtotal_note([item('A', None)], 0) == []
