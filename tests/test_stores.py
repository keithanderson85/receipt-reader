"""Stores as people think of them: merged brands, numbered stores, one-time number assignment, exact filters."""
import re
import sqlite3
from datetime import datetime, timedelta
from urllib.parse import quote

import pytest

TODAY = datetime.now().date()
RECENT = (TODAY - timedelta(days=2)).isoformat()


def page(client, query=''):
    res = client.get('/spending' + query)
    assert res.status_code == 200
    return res.get_data(as_text=True)


def bars(html):
    return re.findall(r'data-name="([^"]*)" data-value="(\$[\d,\.]+)"\s+data-detail="[^"]*receipt', html)


def money(text):
    return float(text.replace('$', '').replace(',', ''))


@pytest.fixture
def pawn(add_location, add_expense):
    """Two saved Metro Pawn locations, receipts with and without addresses, and some EZPAWN stores."""
    keystone = add_location('Metro Pawn Keystone', '800 W. 5th St NV 89503')
    oddie = add_location('Metro Pawn Oddie', '2625 Oddie Blvd NV 89512')
    add_expense(merchant='Metro Pawn Keystone', amount=1000.0, date=RECENT, address='800 W. 5th St NV 89503', location='Reno, NV')
    add_expense(merchant='Metro Pawn Oddie', amount=300.0, date=RECENT, address='2625 Oddie Blvd NV 89512', location='Reno, NV')
    add_expense(merchant='Metro Pawn #E0205', amount=200.0, date=RECENT, address=None)          # a number, no address
    add_expense(merchant='Metro Pawn #E205', amount=50.0, date=RECENT, address=None)            # same number, typo'd
    add_expense(merchant='Metro Pawn', amount=80.0, date=RECENT, address=None)                  # nothing to go on
    add_expense(merchant='EZPAWN #E0101', amount=100.0, date=RECENT, address=None)
    add_expense(merchant='EZPAWN #EU101', amount=25.0, date=RECENT, address=None)               # typo of E0101
    add_expense(merchant='EZPWN HE0114', amount=60.0, date=RECENT, address=None)
    return {'keystone': keystone, 'oddie': oddie}


# ---- the headline fix: many spellings, few stores ----------------------------------------------------------

def test_by_store_merges_spellings_and_numbered_stores(client, pawn):
    got = dict(bars(page(client, '?period=all')))
    assert got['Metro Pawn Keystone'] == '$1,000.00' and got['Metro Pawn Oddie'] == '$300.00'
    assert got['EZPAWN #E0101'] == '$125.00'                                   # E0101 and its EU101 typo are one store
    assert got['EZPAWN #E0114'] == '$60.00'                                    # EZPWN HE0114 repaired
    assert got['Metro Pawn #E0205'] == '$250.00'                              # E0205 and E205
    assert got['Metro Pawn (no location)'] == '$80.00'
    assert not any('EZPWN' in name or 'EU101' in name for name in got)


def test_by_brand_rolls_everything_up(client, pawn):
    got = dict(bars(page(client, '?period=all&group=brand')))
    assert money(got['Metro Pawn']) == 1630.0 and money(got['EZPAWN']) == 185.0 and len(got) == 2


def test_brand_drilldown_lists_its_stores_and_they_add_up(client, pawn):
    html = page(client, '?period=all&group=brand&store=Metro Pawn')
    assert 'Spent at Metro Pawn' in html and 'id="childrenTitle"' in html
    children = dict(bars(html))
    assert set(children) == {'Metro Pawn Keystone', 'Metro Pawn Oddie', 'Metro Pawn #E0205', 'Metro Pawn (no location)'}
    assert sum(money(v) for v in children.values()) == 1630.0
    assert 'All brands' in html


def test_store_drilldown_includes_every_receipt_placed_there(client, pawn, add_expense):
    html = page(client, f'?period=all&store={quote("Metro Pawn #E0205")}')
    assert '$250.00' in html and html.count('class="row-item"') == 2


def test_footnote_explains_receipts_that_cannot_be_placed(client, pawn):
    html = page(client, '?period=all')
    assert '(no location)' in html and '1 receipt ($80.00)' in html
    assert 'cannot be placed' in html or "can't be placed" in html or 'can&#39;t be placed' in html


def test_no_footnote_when_everything_is_placed(client, add_expense):
    add_expense(merchant='Home Depot', amount=10.0, date=RECENT)
    assert '(no location)' not in page(client, '?period=all')


# ---- the one-time number assignment -------------------------------------------------------------------------

def test_unassigned_numbers_are_offered_with_the_brands_locations(client, pawn):
    html = page(client, '?period=all')
    assert 'Match store numbers to your locations' in html
    assert 'Metro Pawn #E0205' in html and '2 receipts' in html and '$250.00' in html
    panel = html.split('id="matchTitle"')[1].split('</section>')[0]
    assert 'Metro Pawn Keystone' in panel and 'Metro Pawn Oddie' in panel
    assert 'EZPAWN' not in panel                                              # EZPAWN has no saved locations to choose from


def test_assigning_a_number_moves_those_receipts_into_the_location(client, pawn, db):
    res = client.post('/locations/assign_code', data={'code': 'E0205', 'location_id': pawn['keystone'], 'next': '/spending?period=all'})
    assert res.status_code == 302 and res.headers['Location'].endswith('/spending?period=all')
    assert db('select store_codes from locations where id = ?', pawn['keystone']) == [('E0205',)]

    html = page(client, '?period=all')
    got = dict(bars(html))
    assert got['Metro Pawn Keystone'] == '$1,250.00' and 'Metro Pawn #E0205' not in got
    assert 'Match store numbers to your locations' not in html                 # nothing left to assign


def test_assigning_accepts_sloppy_input_and_never_duplicates(client, pawn, db):
    client.post('/locations/assign_code', data={'code': 'e205', 'location_id': pawn['keystone']})
    client.post('/locations/assign_code', data={'code': 'E0205', 'location_id': pawn['keystone']})
    assert db('select store_codes from locations where id = ?', pawn['keystone']) == [('E0205',)]


def test_assign_rejects_bad_input_and_foreign_locations(client, other_client, pawn, add_location, db):
    theirs = add_location('Bobs Pawn', '1 Elm St', user_id=2)
    client.post('/locations/assign_code', data={'code': 'not a code', 'location_id': pawn['keystone']})
    client.post('/locations/assign_code', data={'code': 'E0205', 'location_id': theirs})                  # not mine
    other_client.post('/locations/assign_code', data={'code': 'E0205', 'location_id': pawn['keystone']})  # not theirs
    assert db('select store_codes from locations where id = ?', pawn['keystone']) == [('',)]
    assert db('select store_codes from locations where id = ?', theirs) == [('',)]


def test_assign_never_redirects_offsite(client, pawn):
    res = client.post('/locations/assign_code', data={'code': 'E0205', 'location_id': pawn['keystone'],
                                                       'next': 'https://evil.example/'})
    assert res.status_code == 302 and 'evil.example' not in res.headers['Location']


# ---- the Locations page ------------------------------------------------------------------------------------------

def test_locations_page_spend_matches_the_spending_page(client, pawn, add_location):
    client.post('/locations/assign_code', data={'code': 'E0205', 'location_id': pawn['keystone']})
    html = client.get('/locations').get_data(as_text=True)
    row = re.search(r'<tr>(?:(?!</tr>).)*Metro Pawn Keystone(?:(?!</tr>).)*</tr>', html, re.S).group(0)
    assert '1250.00' in row                                                  # address receipts + the E0205 receipts
    assert '#E0205' in row


def test_setting_store_numbers_on_a_location_normalises_and_replaces(client, pawn, db):
    res = client.post(f"/locations/{pawn['oddie']}/codes", data={'store_codes': 'e203, E0203  #2030 nonsense'})
    assert res.status_code == 302
    assert db('select store_codes from locations where id = ?', pawn['oddie']) == [('E0203,2030',)]
    client.post(f"/locations/{pawn['oddie']}/codes", data={'store_codes': 'E0210'})
    assert db('select store_codes from locations where id = ?', pawn['oddie']) == [('E0210',)]            # replaced


def test_other_users_cannot_edit_my_location_numbers(other_client, pawn, db):
    other_client.post(f"/locations/{pawn['oddie']}/codes", data={'store_codes': 'E9999'})
    assert db('select store_codes from locations where id = ?', pawn['oddie']) == [('',)]


def test_the_locations_page_shows_numbers_and_an_editor(client, pawn):
    client.post(f"/locations/{pawn['oddie']}/codes", data={'store_codes': 'E0203'})
    html = client.get('/locations').get_data(as_text=True)
    assert '#E0203' in html and 'id="codesModal"' in html and 'data-action="/locations/' in html


# ---- exact store / brand filters on the receipts list and exports ----------------------------------------------------

def test_expenses_list_filters_to_exactly_one_store(client, pawn):
    html = client.get(f'/expenses?store={quote("EZPAWN #E0101")}').get_data(as_text=True)
    assert html.count('class="row-item"') == 2 and 'Store: EZPAWN #E0101' in html


def test_expenses_list_filters_to_a_brand(client, pawn):
    html = client.get('/expenses?brand=Metro Pawn').get_data(as_text=True)
    assert html.count('class="row-item"') == 5


def test_view_all_receipts_from_a_store_matches_the_chart_total(client, pawn):
    page_html = page(client, '?period=all&store=Metro Pawn Keystone')
    link = re.search(r'href="(/expenses\?[^"]*)"[^>]*>View all receipts', page_html).group(1).replace('&amp;', '&')
    listing = client.get(link).get_data(as_text=True)
    assert '$1,000.00' in listing and listing.count('class="row-item"') == 1


def test_export_respects_the_store_filter(client, pawn):
    body = client.get(f'/export/csv?store={quote("EZPAWN #E0101")}').get_data(as_text=True)
    assert 'EZPAWN #E0101' in body and 'EZPAWN #EU101' in body and 'Metro Pawn' not in body.split('SUMMARY')[0]


# ---- migration -----------------------------------------------------------------------------------------------------

def test_existing_databases_gain_the_store_numbers_column(A, tmp_path, monkeypatch):
    old_dir = tmp_path / 'old_install'
    old_dir.mkdir()
    monkeypatch.chdir(old_dir)
    con = sqlite3.connect('receipts.db')
    con.execute('CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, password_hash TEXT, created_at TIMESTAMP)')
    con.execute('CREATE TABLE locations (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, address TEXT, category TEXT)')
    con.execute("INSERT INTO locations (user_id, name, address) VALUES (1, 'Old Location', '1 Main')")
    con.commit(); con.close()
    A.init_db()
    con = sqlite3.connect('receipts.db')
    assert 'store_codes' in [r[1] for r in con.execute('PRAGMA table_info(locations)')]
    assert con.execute('SELECT name, store_codes FROM locations').fetchall() == [('Old Location', None)]
    con.close()
    A.init_db()                                                                  # running it again is harmless


def test_everything_still_renders_with_no_locations_and_no_receipts(client):
    for url in ('/spending', '/spending?group=brand', '/spending?group=city', '/locations'):
        assert client.get(url).status_code == 200
