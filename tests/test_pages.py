"""Page rendering, filtering, exports, editing, and per-user isolation (regression suite)."""
import io
from datetime import date

import pytest

TODAY = date.today().isoformat()


def rows(resp):
    return resp.get_data(as_text=True).count('class="row-item"')


@pytest.fixture
def seeded(add_expense):
    ids = {}
    ids['shell'] = add_expense(merchant='Shell', amount=40.0, date='2025-03-05', category='travel',
                               location='Reno, NV', address='10 Kietzke Ln')
    ids['star'] = add_expense(merchant='Starbucks', amount=6.5, date='2026-10-02', category='meals',
                              location='Sparks, NV', address='5 Victorian Ave')
    ids['deli'] = add_expense(merchant='Joe Deli', amount=18.25, date='2026-10-20', category='meals',
                              location='Reno, NV', address='9 Virginia St',
                              items=[('Sandwich', 9.0, 2)], tax_amount=None)
    ids['pawn'] = add_expense(merchant='Metro Pawn', amount=250.0, date='2026-09-15', category='inventory',
                              location='Reno, NV', address='77 Center St')
    return ids


# ---- access control ---------------------------------------------------------------

@pytest.mark.parametrize('path', ['/', '/expenses', '/capture', '/bulk_upload', '/locations', '/issues',
                                  '/edit_expense/1', '/expense/1/items', '/export/csv', '/review/x.jpg'])
def test_pages_require_login(anon, path):
    res = anon.get(path)
    assert res.status_code == 302 and '/login' in res.headers['Location']


def test_login_and_register_pages_render(anon):
    assert anon.get('/login').status_code == 200
    assert anon.get('/register').status_code == 200


def test_wrong_password_is_rejected(A, client):
    other = A.app.test_client()
    res = other.post('/login', data={'username': 'alice', 'password': 'nope'})
    assert res.status_code == 200 and 'Invalid username or password' in res.get_data(as_text=True)


# ---- pages render ----------------------------------------------------------------

@pytest.mark.parametrize('path', ['/', '/expenses', '/capture', '/bulk_upload', '/locations', '/issues'])
def test_pages_render_with_empty_database(client, path):
    assert client.get(path).status_code == 200


@pytest.mark.parametrize('path', ['/', '/expenses', '/locations', '/issues'])
def test_pages_render_with_data(client, seeded, path):
    assert client.get(path).status_code == 200


def test_navigation_has_phone_tab_bar_and_desktop_links(client):
    html = client.get('/').get_data(as_text=True)
    assert 'class="tabbar"' in html and 'tab-fab' in html and 'top-links' in html
    assert 'Issues' in html


def test_review_hides_tab_bar_for_a_focused_flow(client, A, ocr):
    from conftest import make_jpeg
    res = client.post('/capture/process', content_type='multipart/form-data',
                      data={'photos': [(io.BytesIO(make_jpeg()), 'p.jpg')]})
    html = client.get(res.get_json()['redirect_url']).get_data(as_text=True)
    assert 'class="tabbar"' not in html and 'id="saveBtn"' in html


def test_dashboard_numbers(client, add_expense):
    add_expense(merchant='A', amount=100.0, date=TODAY, location='Reno, NV')
    add_expense(merchant='B', amount=50.5, date=TODAY, location='Sparks, NV')
    add_expense(merchant='Old', amount=1000.0, date='2020-01-01', location='Reno, NV')
    html = client.get('/').get_data(as_text=True)
    assert '$150.50' in html                                  # this month only
    assert '$1,150' in html                                   # all time
    assert 'Reno, NV' in html and 'Sparks, NV' in html


def test_dashboard_empty_state(client):
    assert 'No expenses yet' in client.get('/').get_data(as_text=True)


# ---- expense list filters -----------------------------------------------------------

@pytest.mark.parametrize('query, expected', [
    ('', 4),
    ('?category=meals', 2),
    ('?category=inventory', 1),
    ('?q=star', 1),
    ('?q=nonexistent', 0),
    ('?year=2025', 1),
    ('?year=2026', 3),
    ('?month=10', 2),
    ('?year=2026&month=10', 2),
    ('?start_date=2026-10-01&end_date=2026-10-31', 2),
    ('?start_date=2026-10-03', 1),
    ('?end_date=2025-12-31', 1),
    ('?location=Sparks', 1),                                  # matches the location column
    ('?location=Virginia', 1),                                # matches the address column
    ('?category=meals&month=10&q=deli', 1),
])
def test_expense_filters(client, seeded, query, expected):
    assert rows(client.get('/expenses' + query)) == expected


@pytest.mark.parametrize('sort, first', [
    ('date_desc', 'Joe Deli'), ('date_asc', 'Shell'), ('amount_desc', 'Metro Pawn'),
    ('amount_asc', 'Starbucks'), ('merchant', 'Joe Deli'),
])
def test_expense_sorting(client, seeded, sort, first):
    html = client.get(f'/expenses?sort={sort}').get_data(as_text=True)
    titles = [chunk.split('</div>')[0] for chunk in html.split('class="row-title">')[1:]]
    assert titles[0] == first


def test_expense_list_summary_and_filter_chips(client, seeded):
    html = client.get('/expenses?category=meals').get_data(as_text=True)
    assert '2 expenses' in html and '$24.75' in html
    assert 'chip-remove' in html                              # active filter shown as a removable chip


def test_search_text_is_escaped(client, seeded):
    html = client.get('/expenses?q=<script>alert(1)</script>').get_data(as_text=True)
    assert '<script>alert(1)</script>' not in html


def test_only_own_expenses_are_listed(client, other_client, seeded, add_expense):
    add_expense(user_id=2, merchant='BobsSecretShop')
    assert 'BobsSecretShop' not in client.get('/expenses').get_data(as_text=True)
    assert rows(other_client.get('/expenses')) == 1


# ---- exports ---------------------------------------------------------------------

def test_csv_export_respects_filters(client, seeded):
    res = client.get('/export/csv?category=meals')
    body = res.get_data(as_text=True)
    assert res.mimetype == 'text/csv'
    assert 'Starbucks' in body and 'Joe Deli' in body and 'Shell' not in body


def test_excel_export(client, seeded):
    res = client.get('/export/excel?year=2026')
    assert res.status_code == 200 and 'spreadsheetml' in res.mimetype
    assert res.get_data()[:2] == b'PK'


def test_export_with_no_matches_redirects(client, seeded):
    assert client.get('/export/csv?q=zzz').status_code == 302


# ---- item details page (was broken: wrong columns, loop-scoped totals) ------------------

def test_items_page_shows_items_and_totals(client, seeded):
    html = client.get(f"/expense/{seeded['deli']}/items").get_data(as_text=True)
    assert 'Joe Deli' in html and 'Sandwich' in html
    assert '2 × $9.00' in html and '$18.00' in html           # quantity applied to the line total


def test_items_page_handles_missing_tax_and_no_items(client, seeded):
    assert client.get(f"/expense/{seeded['shell']}/items").status_code == 200
    assert 'No items saved' in client.get(f"/expense/{seeded['shell']}/items").get_data(as_text=True)


def test_items_page_flags_totals_that_do_not_add_up(client, add_expense):
    eid = add_expense(amount=100.0, items=[('Thing', 10.0, 1)], tax_amount=1.0)
    assert 'Check the receipt' in client.get(f'/expense/{eid}/items').get_data(as_text=True)


def test_get_expense_items_json_labels_match_values(client, add_expense):
    eid = add_expense(merchant='Joe Deli', amount=18.25, date='2026-10-20', receipt_filename='r.jpg',
                      items=[('Sandwich', 9.0, 2)])
    data = client.get(f'/get_expense_items/{eid}').get_json()['expense']
    assert (data['merchant'], data['date'], data['receipt_file'], data['amount']) == ('Joe Deli', '2026-10-20', 'r.jpg', 18.25)
    assert data['items_total'] == 18.0 and 'user_id' not in data


# ---- editing ------------------------------------------------------------------------

def test_edit_page_prefills_values_and_items(client, seeded):
    html = client.get(f"/edit_expense/{seeded['deli']}").get_data(as_text=True)
    assert 'value="Joe Deli"' in html and 'value="Sandwich"' in html
    assert 'name="location" id="location" value="Reno, NV"' in html
    assert 'Update expense' in html


def test_update_keeps_location_and_rewrites_items_with_quantity(client, seeded, db):
    eid = seeded['deli']
    res = client.post(f'/update_expense/{eid}', data={
        'merchant_name': 'Joe Deli 2', 'amount': '20.00', 'date': '2026-10-20', 'category': 'meals',
        'location': 'Reno, NV', 'address': '9 Virginia St', 'subtotal': '18', 'tax_amount': '2',
        'discount_amount': '0', 'items_count': '1',
        'item_0_description': 'Wrap', 'item_0_price': '6.00', 'item_0_quantity': '3'})
    assert res.status_code == 302
    assert db('select merchant_name, location, amount from expenses where id=?', eid) == [('Joe Deli 2', 'Reno, NV', 20.0)]
    assert db('select description, quantity from receipt_items where expense_id=?', eid) == [('Wrap', 3)]


def test_delete_expense_removes_row_and_file(A, client, add_expense, db):
    import os
    path = os.path.join(A.app.config['UPLOAD_FOLDER'], 'gone.jpg')
    open(path, 'wb').write(b'x')
    eid = add_expense(receipt_filename='gone.jpg')
    client.get(f'/delete_expense/{eid}')
    assert db('select count(*) from expenses')[0][0] == 0 and not os.path.exists(path)


def test_inline_field_update_and_item_update(client, add_expense, db):
    eid = add_expense(amount=5.0, items=[('Thing', 1.0, 1)])
    assert client.post(f'/update_expense_field/{eid}', json={'field': 'merchant_name', 'value': 'Renamed'}).get_json()['success']
    assert client.post(f'/update_expense_field/{eid}', json={'field': 'user_id', 'value': 9}).get_json()['success'] is False
    item_id = db('select id from receipt_items')[0][0]
    assert client.post(f'/update_item/{item_id}', json={'field': 'quantity', 'value': 4}).get_json()['success']
    assert db('select merchant_name from expenses')[0][0] == 'Renamed'
    assert db('select quantity from receipt_items')[0][0] == 4


# ---- isolation between users (regression: scoping that already works) ----------------------

def test_other_users_cannot_read_edit_or_delete_my_expenses(client, other_client, add_expense, db):
    eid = add_expense(merchant='AlicePrivate', items=[('x', 1.0, 1)])
    assert 'AlicePrivate' not in other_client.get(f'/edit_expense/{eid}', follow_redirects=True).get_data(as_text=True)
    assert 'AlicePrivate' not in other_client.get(f'/expense/{eid}/items', follow_redirects=True).get_data(as_text=True)
    assert other_client.get(f'/get_expense_items/{eid}').get_json()['success'] is False
    other_client.get(f'/delete_expense/{eid}')
    other_client.post(f'/update_expense_field/{eid}', json={'field': 'merchant_name', 'value': 'Hacked'})
    item_id = db('select id from receipt_items')[0][0]
    assert other_client.post(f'/update_item/{item_id}', json={'field': 'price', 'value': 99}).status_code == 403
    assert other_client.post(f'/delete_item/{item_id}').status_code == 403
    assert db('select merchant_name from expenses') == [('AlicePrivate',)]
    assert db('select price from receipt_items') == [(1.0,)]


def test_update_expense_cannot_change_another_users_row(client, other_client, add_expense, db):
    eid = add_expense(merchant='AlicePrivate')
    other_client.post(f'/update_expense/{eid}', data={
        'merchant_name': 'Hacked', 'amount': '1', 'date': '2026-01-01', 'category': 'meals'})
    assert db('select merchant_name from expenses') == [('AlicePrivate',)]


# ---- known defects, pinned so they are fixed on purpose (strict xfail flips when fixed) ----------

@pytest.mark.xfail(strict=True, reason='known: update_expense deletes receipt_items before checking ownership')
def test_update_expense_does_not_wipe_another_users_items(client, other_client, add_expense, db):
    eid = add_expense(items=[('Keep me', 5.0, 1)])
    other_client.post(f'/update_expense/{eid}', data={
        'merchant_name': 'x', 'amount': '1', 'date': '2026-01-01', 'category': 'meals'})
    assert db('select description from receipt_items') == [('Keep me',)]


@pytest.mark.xfail(strict=True, reason='known: /uploads/<file> is served without login')
def test_uploads_require_login(A, anon):
    import os
    open(os.path.join(A.app.config['UPLOAD_FOLDER'], 'secret.jpg'), 'wb').write(b'x')
    assert anon.get('/uploads/secret.jpg').status_code in (302, 401, 403)


@pytest.mark.xfail(strict=True, reason='known: foreign keys are off, so deleting an expense orphans its items')
def test_deleting_an_expense_removes_its_items(client, add_expense, db):
    eid = add_expense(items=[('x', 1.0, 1)])
    client.get(f'/delete_expense/{eid}')
    assert db('select count(*) from receipt_items')[0][0] == 0


@pytest.mark.xfail(strict=True, reason='known: login redirects to any ?next= URL (open redirect)')
def test_login_does_not_redirect_offsite(A, client):
    fresh = A.app.test_client()
    res = fresh.post('/login?next=https://evil.example/', data={'username': 'alice', 'password': 'secret1'})
    assert 'evil.example' not in res.headers.get('Location', '')
