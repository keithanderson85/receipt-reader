"""Multi-photo capture flow, review drafts, and saving."""
import io
import os
import re
import time

import pytest

from conftest import item, make_jpeg


def post_photos(client, *files):
    return client.post('/capture/process', content_type='multipart/form-data',
                       data={'photos': [(io.BytesIO(data), name) for name, data in files]})


def review_name(resp):
    return resp.get_json()['redirect_url'].rsplit('/', 1)[1]


def uploads(A):
    folder = A.app.config['UPLOAD_FOLDER']
    return sorted(n for n in os.listdir(folder) if os.path.isfile(os.path.join(folder, n)))


def drafts(A):
    return sorted(os.listdir(A.OCR_CACHE_FOLDER))


# ---- access ------------------------------------------------------------------

def test_capture_requires_login(anon):
    assert anon.get('/capture').status_code == 302
    assert anon.post('/capture/process').status_code == 302


def test_capture_page_renders(client):
    html = client.get('/capture').get_data(as_text=True)
    assert 'Take photo' in html and 'From gallery' in html and 'Save receipt' in html


# ---- processing ----------------------------------------------------------------

def test_exact_duplicate_photo_is_skipped_before_ocr(A, client, ocr):
    a, b = make_jpeg('white', 'a'), make_jpeg('gray', 'b')
    ocr.response['items'] = [item('X', 10, 1)]
    res = post_photos(client, ('1.jpg', a), ('2.jpg', b), ('3.jpg', a))

    assert res.get_json()['success']
    assert ocr.calls[0]['images'] == 2                       # the copy never reached the model
    html = client.get(res.get_json()['redirect_url']).get_data(as_text=True)
    assert 'exact copy of photo 1' in html


def test_overlapping_photos_are_merged_and_reported(A, client, ocr):
    ocr.response['items'] = [item('MILK', 3, 1), item('EGGS', 2, 1), item('EGGS', 2, 2), item('BREAD', 5, 2)]
    ocr.response['subtotal'] = 10
    res = post_photos(client, ('1.jpg', make_jpeg('white', 'a')), ('2.jpg', make_jpeg('gray', 'b')))
    html = client.get(res.get_json()['redirect_url']).get_data(as_text=True)

    assert 'overlap by 1 item' in html
    assert html.count('value="EGGS"') == 1 and 'value="BREAD"' in html and 'value="MILK"' in html
    assert uploads(A) == [review_name(res)]                  # only the stitched receipt remains
    assert review_name(res).endswith('_receipt.jpg')


def test_single_photo_is_processed_without_stitching(A, client, ocr):
    ocr.response['items'] = [item('A', 10)]
    res = post_photos(client, ('p.jpg', make_jpeg()))
    assert res.get_json()['success']
    assert ocr.calls[0]['images'] == 1 and 'MULTI-PHOTO' not in ocr.calls[0]['prompt']
    assert review_name(res).endswith('_1.jpg')


@pytest.mark.parametrize('files, status', [
    ([], 400),
    ([('a.gif', b'GIF89a')], 400),
    ([('a.heic', b'xx')], 400),
    ([(f'{i}.jpg', make_jpeg(label=str(i))) for i in range(9)], 400),
])
def test_bad_capture_requests_are_rejected_cleanly(A, client, ocr, files, status):
    res = post_photos(client, *files)
    assert res.status_code == status and res.get_json()['success'] is False
    assert uploads(A) == [] and ocr.calls == []


def test_ocr_failure_returns_error_and_leaves_no_files(A, client, ocr):
    ocr.error = RuntimeError('model down')
    res = post_photos(client, ('1.jpg', make_jpeg('white')), ('2.jpg', make_jpeg('gray')))
    assert res.status_code == 500 and 'model down' in res.get_json()['error']
    assert uploads(A) == [] and drafts(A) == []


# ---- review draft lifecycle -------------------------------------------------------

def test_review_draft_survives_a_refresh(A, client, ocr):
    ocr.response['items'] = [item('A', 10)]
    url = post_photos(client, ('p.jpg', make_jpeg())).get_json()['redirect_url']
    assert client.get(url).status_code == 200
    assert client.get(url).status_code == 200                # second load still works
    assert len(drafts(A)) == 1


def test_draft_is_private_to_its_owner(A, client, other_client, ocr):
    url = post_photos(client, ('p.jpg', make_jpeg())).get_json()['redirect_url']
    res = other_client.get(url)
    assert res.status_code == 302                            # bob can't open alice's draft


def test_save_removes_the_draft(A, client, ocr, db):
    res = post_photos(client, ('p.jpg', make_jpeg()))
    name = review_name(res)
    client.post('/save_expense', data={
        'merchant_name': 'Test Mart', 'amount': '12.00', 'date': '2026-10-01', 'category': 'meals',
        'filename': name, 'file_hash': 'h'})
    assert drafts(A) == []
    assert db('select count(*) from expenses')[0][0] == 1


def test_discarding_deletes_file_and_draft(A, client, ocr):
    name = review_name(post_photos(client, ('p.jpg', make_jpeg())))
    res = client.post('/delete_uploaded_file', json={'filename': name})
    assert res.get_json()['success']
    assert uploads(A) == [] and drafts(A) == []


def test_stale_drafts_are_pruned(A, client, ocr):
    stale = os.path.join(A.OCR_CACHE_FOLDER, '1_old.jpg.json')
    open(stale, 'w').write('{}')
    old = time.time() - 3 * 24 * 3600
    os.utime(stale, (old, old))
    post_photos(client, ('p.jpg', make_jpeg()))
    assert not os.path.exists(stale) and len(drafts(A)) == 1


def test_review_without_a_draft_redirects_home(client):
    res = client.get('/review/nothing-here.jpg')
    assert res.status_code == 302


# ---- review page content -----------------------------------------------------------

def test_category_is_preselected_from_merchant(A, client, ocr):
    ocr.response['merchant_name'] = 'Metro Pawn'
    html = client.get(post_photos(client, ('p.jpg', make_jpeg())).get_json()['redirect_url']).get_data(as_text=True)
    assert re.search(r'<option selected value="inventory">', html)


def test_saved_store_category_wins_over_guess(A, client, ocr, db):
    import sqlite3
    con = sqlite3.connect('receipts.db')
    con.execute("INSERT INTO locations (user_id, name, address, category) VALUES (1, 'My Garage Store', '1 Main St, Reno, NV 89501', 'equipment')")
    con.commit(); con.close()
    html = client.get(post_photos(client, ('p.jpg', make_jpeg())).get_json()['redirect_url']).get_data(as_text=True)
    assert re.search(r'<option selected value="equipment">', html)
    assert 'value="My Garage Store"' in html


def test_duplicate_of_saved_receipt_is_flagged(A, client, ocr, add_expense):
    add_expense(merchant='Test Mart', amount=12.0, date='2026-10-01')
    html = client.get(post_photos(client, ('p.jpg', make_jpeg())).get_json()['redirect_url']).get_data(as_text=True)
    assert 'already saved' in html and 'id="duplicateWarning"' in html


def test_long_receipt_draft_is_not_limited_by_cookie_size(A, client, ocr):
    ocr.response['items'] = [item(f'LINE ITEM NUMBER {i} WITH A LONG DESCRIPTION', 1.0 + i) for i in range(120)]
    res = post_photos(client, ('p.jpg', make_jpeg()))
    html = client.get(res.get_json()['redirect_url']).get_data(as_text=True)
    assert html.count('class="item-row" data-item>') == 120


# ---- saving what the review form posts ---------------------------------------------

def form(**over):
    base = {'merchant_name': 'Test Mart', 'amount': '12.00', 'date': '2026-10-01', 'category': 'meals',
            'location': 'Reno, NV', 'address': '1 Main St', 'filename': 'x.jpg', 'file_hash': 'h',
            'subtotal': '10.00', 'tax_amount': '2.00', 'discount_amount': '0', 'tax_rate': '8'}
    base.update(over)
    return base


def test_item_quantities_and_location_are_saved(client, db):
    client.post('/save_expense', data=form(
        items_count='2',
        item_0_description='MILK', item_0_price='3.50', item_0_quantity='3', item_0_sku='S1', item_0_raw_line='raw',
        item_1_description='BREAD', item_1_price='2.00', item_1_quantity='1'))
    assert db('select merchant_name, location, category from expenses') == [('Test Mart', 'Reno, NV', 'meals')]
    assert db('select description, price, quantity, sku from receipt_items order by id') == [
        ('MILK', 3.5, 3, 'S1'), ('BREAD', 2.0, 1, None)]


def test_blank_item_rows_are_ignored(client, db):
    client.post('/save_expense', data=form(
        items_count='3',
        item_0_description='OK', item_0_price='1.00', item_0_quantity='1',
        item_1_description='', item_1_price='', item_1_quantity='1',
        item_2_description='NO PRICE', item_2_price='', item_2_quantity='1'))
    assert db('select description from receipt_items') == [('OK',)]


def test_invalid_expense_is_not_saved(client, db):
    res = client.post('/save_expense', data=form(amount='0'))
    assert res.status_code == 302
    assert db('select count(*) from expenses')[0][0] == 0
