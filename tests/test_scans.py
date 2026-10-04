"""Snap and go: save now, quick read + duplicate check, background full read, inbox approval."""
import io
import os
import time

import pytest

import scan_store
from conftest import item, make_jpeg


def submit(client, *photos, **extra):
    files = photos or (make_jpeg('white', 'a'),)
    return client.post('/capture/submit', content_type='multipart/form-data',
                       data={'photos': [(io.BytesIO(b), f'{i}.jpg') for i, b in enumerate(files)], **extra})


def status(client, res):
    return client.get(res.get_json()['status_url']).get_json()


def new_scan(client, ocr, photo='a', **response):
    ocr.response.update(response)
    ocr.response.setdefault('items', [item('WIDGET', 10.0, qty=2)])
    return submit(client, make_jpeg('white', photo))


@pytest.fixture
def ocr_ready(ocr):
    ocr.response.update(merchant_name='Test Mart', amount=20.0, date='2026-10-01', subtotal=20.0, tax_amount=0,
                        items=[item('WIDGET', 10.0, qty=2)])
    return ocr


# ---- submitting ----------------------------------------------------------------------------

def test_submit_returns_a_scan_and_status_url_immediately(A, client, ocr_ready):
    res = submit(client)
    data = res.get_json()
    assert res.status_code == 200 and data['success'] and data['scan_id'] and data['status_url']


def test_submit_requires_login(anon):
    assert anon.post('/capture/submit').status_code == 302


@pytest.mark.parametrize('files', [[], [b'GIF89a'], [make_jpeg(label=str(i)) for i in range(9)]])
def test_bad_submissions_create_no_scan(A, client, ocr_ready, files):
    data = {'photos': [(io.BytesIO(b), f'{i}.gif' if b == b'GIF89a' else f'{i}.jpg') for i, b in enumerate(files)]}
    res = client.post('/capture/submit', content_type='multipart/form-data', data=data)
    assert res.status_code == 400 and res.get_json()['success'] is False
    assert scan_store.list_scans(1, ('processing', 'ready', 'needs_review', 'error')) == []


# ---- quick read + full read ---------------------------------------------------------------------

def test_quick_read_comes_first_then_full_read_with_a_clean_result(A, client, ocr_ready):
    res = submit(client)
    s = status(client, res)
    assert [c['kind'] for c in ocr_ready.calls] == ['quick', 'full']
    assert (s['status'], s['quick_status'], s['dup_status']) == ('ready', 'done', 'new')
    assert (s['merchant'], s['amount'], s['date']) == ('Test Mart', 20.0, '2026-10-01')
    assert s['review_url'] and s['reasons'] == []


def test_quick_pass_reads_only_the_top_and_bottom_photos_of_a_long_receipt(A, client, ocr_ready):
    submit(client, make_jpeg('white', 'a'), make_jpeg('gray', 'b'), make_jpeg('lightgray', 'c'))
    assert ocr_ready.calls[0]['kind'] == 'quick' and ocr_ready.calls[0]['images'] == 2
    assert ocr_ready.calls[1]['kind'] == 'full' and ocr_ready.calls[1]['images'] == 3


def test_quick_read_failure_does_not_stop_the_full_read(A, client, ocr_ready):
    ocr_ready.quick_error = RuntimeError('quick down')
    s = status(client, submit(client))
    assert s['quick_status'] == 'failed' and s['status'] == 'ready' and s['dup_status'] == 'new'


def test_unreadable_quick_answer_is_treated_as_a_miss(A, client, ocr_ready):
    ocr_ready.quick_response = 'not json'
    assert status(client, submit(client))['quick_status'] == 'failed'


def test_full_read_failure_marks_the_scan_as_retryable_and_keeps_the_photo(A, client, ocr_ready):
    ocr_ready.error = RuntimeError('model down')
    res = submit(client)
    s = status(client, res)
    assert s['status'] == 'error' and 'model down' in s['error']
    assert len([n for n in os.listdir(A.app.config['UPLOAD_FOLDER']) if n.endswith('.jpg')]) == 1

    ocr_ready.error = None
    scan_id = res.get_json()['scan_id']
    client.post(f'/inbox/{scan_id}/retry')
    assert status(client, res)['status'] == 'ready'


def test_missing_date_needs_review(A, client, ocr_ready):
    ocr_ready.response['date'] = None
    s = status(client, submit(client))
    assert s['status'] == 'needs_review' and 'Date missing' in s['reasons']


def test_missing_total_needs_review(A, client, ocr_ready):
    ocr_ready.response['amount'] = None
    assert 'Total missing or unreadable' in status(client, submit(client))['reasons']


def test_scan_runs_in_the_background_when_not_in_test_mode(A, client, ocr_ready):
    A.app.config['SCANS_SYNC'] = False
    res = submit(client)
    deadline = time.time() + 10
    s = status(client, res)
    while s['status'] == 'processing' and time.time() < deadline:
        time.sleep(0.1)
        s = status(client, res)
    assert s['status'] == 'ready'


def test_scans_stuck_processing_become_retryable_errors(A, client, ocr_ready):
    A.app.config['SCANS_SYNC'] = False
    A.SCAN_EXECUTOR.shutdown(wait=True)                                   # nothing will pick this one up
    scan_id = scan_store.create_scan(1, 'x.jpg', 'h', photo_paths=['x.jpg'])
    conn = scan_store.connect()
    conn.execute("UPDATE scans SET updated_at = '2000-01-01T00:00:00' WHERE id = ?", (scan_id,))
    conn.commit(); conn.close()
    client.get('/inbox')
    scan = scan_store.get_scan(scan_id)
    assert scan['status'] == 'error' and 'Retry' in scan['error']
    # restore an executor for later tests in this session
    from concurrent.futures import ThreadPoolExecutor
    A.SCAN_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix='scan-reader')


# ---- duplicate checks -------------------------------------------------------------------------------

def test_duplicate_of_a_saved_expense_is_flagged_by_the_quick_pass(A, client, ocr_ready, add_expense):
    add_expense(merchant='Test Mart', amount=20.0, date='2026-10-01')
    s = status(client, submit(client))
    assert s['dup_status'] == 'duplicate' and s['status'] == 'needs_review'
    assert s['matches'][0]['kind'] == 'expense' and s['matches'][0]['reason'] == 'same_amount_date'
    assert 'Looks like a duplicate' in s['reasons']


def test_same_total_and_date_at_a_different_store_is_only_a_maybe(A, client, ocr_ready, add_expense):
    add_expense(merchant='Completely Different Place', amount=20.0, date='2026-10-01')
    s = status(client, submit(client))
    assert s['dup_status'] == 'maybe' and s['status'] == 'needs_review'


def test_different_total_or_date_is_new(A, client, ocr_ready, add_expense):
    add_expense(merchant='Test Mart', amount=20.01, date='2026-10-01')
    add_expense(merchant='Test Mart', amount=20.0, date='2026-10-02')
    assert status(client, submit(client))['dup_status'] == 'new'


def test_scanning_the_same_receipt_twice_before_approving_either_is_caught(A, client, ocr_ready):
    first = submit(client, make_jpeg('white', 'first'))
    second = submit(client, make_jpeg('gray', 'second photo of the same receipt'))
    assert status(client, first)['dup_status'] == 'new'
    s = status(client, second)
    assert s['dup_status'] == 'duplicate' and s['matches'][0]['kind'] == 'scan'


def test_resubmitting_the_identical_photo_is_caught_by_file_hash(A, client, ocr_ready):
    photo = make_jpeg('white', 'same')
    submit(client, photo)
    ocr_ready.response['amount'] = 99.0                                 # even if the totals read differently
    s = status(client, submit(client, photo))
    assert s['dup_status'] == 'duplicate' and s['matches'][0]['reason'] == 'same_file'


def test_other_users_receipts_are_never_matches(A, client, other_client, ocr_ready, add_expense):
    add_expense(user_id=2, merchant='Test Mart', amount=20.0, date='2026-10-01')
    assert status(client, submit(client))['dup_status'] == 'new'


# ---- the inbox -------------------------------------------------------------------------------------

def test_inbox_renders_empty_and_with_every_kind_of_scan(A, client, ocr_ready, add_expense):
    assert 'Nothing waiting' in client.get('/inbox').get_data(as_text=True)

    submit(client, make_jpeg('white', 'clean'))                                           # ready
    ocr_ready.response.update(amount=33.0, date=None)
    submit(client, make_jpeg('gray', 'nodate'))                                           # needs_review
    ocr_ready.response.update(amount=44.0, date='2026-10-05')
    ocr_ready.error = RuntimeError('boom')
    submit(client, make_jpeg('black', 'broken'))                                          # error
    html = client.get('/inbox').get_data(as_text=True)
    assert html.count('class="card inbox-card') == 3
    assert 'Approve 1 clean' in html and 'Retry' in html and 'Date missing' in html and 'New receipt' in html


def test_inbox_count_shows_in_the_navigation(A, client, ocr_ready):
    submit(client)
    html = client.get('/').get_data(as_text=True)
    assert 'tab-badge' in html and 'rel="manifest"' in html
    assert '1 receipt waiting in your inbox' in html


def test_inbox_requires_login(anon):
    assert anon.get('/inbox').status_code == 302


# ---- approving -------------------------------------------------------------------------------------

def test_approve_creates_the_expense_with_items_and_quantities(A, client, ocr_ready, db):
    ocr_ready.response['items'] = [item('WIDGET', 10.0, qty=2, sku='W1'), item('GADGET', 5.5)]
    ocr_ready.response.update(subtotal=25.5, amount=25.5)
    res = submit(client)
    scan_id = res.get_json()['scan_id']
    client.post(f'/inbox/{scan_id}/approve')

    assert db('select merchant_name, amount, date, category, receipt_filename from expenses')[0][:3] == ('Test Mart', 25.5, '2026-10-01')
    assert db('select description, price, quantity, sku from receipt_items order by id') == [
        ('WIDGET', 10.0, 2, 'W1'), ('GADGET', 5.5, 1, None)]
    assert scan_store.get_scan(scan_id)['status'] == 'saved'
    assert 'Nothing waiting' in client.get('/inbox').get_data(as_text=True)


def test_approving_twice_does_not_create_two_expenses(A, client, ocr_ready, db):
    scan_id = submit(client).get_json()['scan_id']
    client.post(f'/inbox/{scan_id}/approve')
    client.post(f'/inbox/{scan_id}/approve')
    assert db('select count(*) from expenses')[0][0] == 1


def test_approving_rechecks_duplicates_saved_since_the_scan(A, client, ocr_ready, add_expense, db):
    scan_id = submit(client).get_json()['scan_id']                       # was 'new' when scanned...
    add_expense(merchant='Test Mart', amount=20.0, date='2026-10-01')    # ...then the same receipt got saved elsewhere
    client.post(f'/inbox/{scan_id}/approve')
    assert db('select count(*) from expenses')[0][0] == 1                # blocked
    assert scan_store.get_scan(scan_id)['status'] == 'needs_review'


def test_save_anyway_overrides_a_duplicate(A, client, ocr_ready, add_expense, db):
    add_expense(merchant='Test Mart', amount=20.0, date='2026-10-01')
    scan_id = submit(client).get_json()['scan_id']
    client.post(f'/inbox/{scan_id}/approve')
    assert db('select count(*) from expenses')[0][0] == 1
    client.post(f'/inbox/{scan_id}/approve', data={'force': '1'})
    assert db('select count(*) from expenses')[0][0] == 2


def test_a_scan_missing_details_cannot_be_approved_directly(A, client, ocr_ready, db):
    ocr_ready.response['date'] = None
    scan_id = submit(client).get_json()['scan_id']
    client.post(f'/inbox/{scan_id}/approve')
    assert db('select count(*) from expenses')[0][0] == 0


def test_approve_all_clean_saves_only_clean_ones(A, client, ocr_ready, add_expense, db):
    submit(client, make_jpeg('white', 'one'))                                             # clean ($20 / Oct 1)
    ocr_ready.response.update(amount=31.0)
    submit(client, make_jpeg('gray', 'two'))                                              # clean ($31)
    ocr_ready.response.update(amount=42.0, date=None)
    submit(client, make_jpeg('black', 'three'))                                           # needs review
    client.post('/inbox/approve_ready')
    assert sorted(r[0] for r in db('select amount from expenses')) == [20.0, 31.0]
    assert len(scan_store.list_scans(1)) == 1


def test_approve_all_clean_holds_back_a_scan_that_became_a_duplicate(A, client, ocr_ready, add_expense, db):
    submit(client)
    add_expense(merchant='Test Mart', amount=20.0, date='2026-10-01')
    client.post('/inbox/approve_ready')
    assert db('select count(*) from expenses')[0][0] == 1


# ---- review, discard, retry -----------------------------------------------------------------------------------

def test_review_opens_the_review_screen_and_saving_it_closes_the_scan(A, client, ocr_ready, db):
    scan_id = submit(client).get_json()['scan_id']
    res = client.get(f'/inbox/{scan_id}/review')
    assert res.status_code == 302 and '/review/' in res.headers['Location']
    page = client.get(res.headers['Location']).get_data(as_text=True)
    assert f'name="scan_id" value="{scan_id}"' in page and 'value="Test Mart"' in page

    filename = res.headers['Location'].rsplit('/', 1)[1]
    client.post('/save_expense', data={
        'merchant_name': 'Test Mart (edited)', 'amount': '21.00', 'date': '2026-10-01', 'category': 'meals',
        'filename': filename, 'file_hash': 'h', 'scan_id': scan_id})
    assert scan_store.get_scan(scan_id)['status'] == 'saved'
    assert db('select merchant_name from expenses') == [('Test Mart (edited)',)]


def test_discarding_from_the_review_screen_closes_the_scan(A, client, ocr_ready):
    scan_id = submit(client).get_json()['scan_id']
    filename = scan_store.get_scan(scan_id)['filename']
    client.post('/delete_uploaded_file', json={'filename': filename})
    assert scan_store.get_scan(scan_id)['status'] == 'discarded'


def test_discard_deletes_the_photo_and_original(A, client, ocr_ready):
    scan_id = submit(client).get_json()['scan_id']
    folder = A.app.config['UPLOAD_FOLDER']
    name = scan_store.get_scan(scan_id)['filename']
    assert os.path.exists(os.path.join(folder, name))
    client.post(f'/inbox/{scan_id}/discard')
    assert not os.path.exists(os.path.join(folder, name))
    assert scan_store.get_scan(scan_id)['status'] == 'discarded'


def test_retry_refuses_when_there_is_nothing_to_retry(A, client, ocr_ready):
    scan_id = submit(client).get_json()['scan_id']                       # ready, not an error
    client.post(f'/inbox/{scan_id}/retry')
    assert scan_store.get_scan(scan_id)['status'] == 'ready'


# ---- isolation -------------------------------------------------------------------------------------------

def test_other_users_cannot_see_or_touch_my_scans(A, client, other_client, ocr_ready, db):
    scan_id = submit(client).get_json()['scan_id']
    assert other_client.get(f'/scans/{scan_id}/status').status_code == 404
    other_client.post(f'/inbox/{scan_id}/approve')
    other_client.post(f'/inbox/{scan_id}/discard')
    assert other_client.get(f'/inbox/{scan_id}/review').status_code == 302
    assert scan_store.get_scan(scan_id)['status'] == 'ready' and db('select count(*) from expenses')[0][0] == 0
    assert 'Nothing waiting' in other_client.get('/inbox').get_data(as_text=True)


def test_review_cannot_close_someone_elses_scan(A, client, other_client, ocr_ready):
    scan_id = submit(client).get_json()['scan_id']
    other_client.post('/save_expense', data={
        'merchant_name': 'X', 'amount': '1.00', 'date': '2026-10-01', 'category': 'meals', 'filename': 'x.jpg',
        'scan_id': scan_id})
    assert scan_store.get_scan(scan_id)['status'] == 'ready'


# ---- the quick-read module itself ---------------------------------------------------------------------------------

def test_quick_read_parses_dates_and_amounts(A, ocr):
    from receipt_ocr_genai import ReceiptOCRGenAI
    assert ReceiptOCRGenAI.parse_receipt_date('2026-10-01') == '2026-10-01'
    assert ReceiptOCRGenAI.parse_receipt_date('10/01/2026') == '2026-10-01'
    assert ReceiptOCRGenAI.parse_receipt_date('2026-10-01T13:35:00') == '2026-10-01'
    assert ReceiptOCRGenAI.parse_receipt_date('garbage') is None and ReceiptOCRGenAI.parse_receipt_date(None) is None


def test_quick_read_never_raises(A, ocr, tmp_path):
    ocr.quick_response = {'merchant_name': ' Shell ', 'amount': '12.345', 'date': '10/02/2026'}
    path = tmp_path / 'q.jpg'
    path.write_bytes(make_jpeg())
    assert A.receipt_ocr.quick_read([str(path)]) == {'merchant_name': 'Shell', 'amount': 12.35, 'date': '2026-10-02'}
    assert A.receipt_ocr.quick_read([str(tmp_path / 'missing.jpg')]) is None
    assert A.receipt_ocr.quick_read([]) is None
