"""Installable home-screen app: manifest, icons, service worker, staying signed in."""
import json
from pathlib import Path

import pytest
from PIL import Image

STATIC = Path(__file__).resolve().parents[1] / 'static'


def manifest(client):
    res = client.get('/manifest.webmanifest')
    assert res.status_code == 200
    return res, json.loads(res.get_data(as_text=True))


# ---- manifest ---------------------------------------------------------------------------

def test_manifest_is_served_without_login_with_the_right_type(anon):
    res, data = manifest(anon)
    assert res.mimetype == 'application/manifest+json'
    assert data['display'] == 'standalone' and data['name'] and data['short_name']


def test_app_opens_on_the_scan_screen(anon):
    _, data = manifest(anon)
    assert data['start_url'].startswith('/capture')
    assert data['scope'] == '/' and data['start_url'].startswith(data['scope'])


def test_manifest_icons_exist_and_have_the_declared_sizes(anon):
    _, data = manifest(anon)
    sizes = {(i['sizes'], i['purpose']) for i in data['icons']}
    assert ('192x192', 'any') in sizes and ('512x512', 'any') in sizes and ('512x512', 'maskable') in sizes
    for icon in data['icons']:
        path = STATIC / icon['src'].replace('/static/', '', 1)
        assert path.is_file(), icon['src']
        w, h = (int(n) for n in icon['sizes'].split('x'))
        with Image.open(path) as im:
            assert im.format == 'PNG' and im.size == (w, h)


def test_manifest_shortcuts_point_at_real_pages(A, client):
    _, data = manifest(client)
    for shortcut in data['shortcuts']:
        assert client.get(shortcut['url']).status_code == 200


def test_maskable_icon_is_full_bleed_and_regular_icon_is_rounded():
    with Image.open(STATIC / 'icons' / 'icon-maskable-512.png') as im:
        assert im.convert('RGBA').getpixel((2, 2))[3] == 255            # corner filled: the OS applies its own mask
    with Image.open(STATIC / 'icons' / 'icon-512.png') as im:
        assert im.convert('RGBA').getpixel((2, 2))[3] == 0              # rounded: corner transparent


# ---- service worker -------------------------------------------------------------------------

def test_service_worker_is_served_from_the_root_with_site_wide_scope(anon):
    res = anon.get('/sw.js')
    assert res.status_code == 200 and res.mimetype == 'application/javascript'
    assert res.headers['Service-Worker-Allowed'] == '/'
    assert 'no-cache' in res.headers['Cache-Control']                    # updates are picked up promptly
    assert "addEventListener('fetch'" in res.get_data(as_text=True)


def test_service_worker_never_caches_pages():
    source = (STATIC / 'sw.js').read_text(encoding='utf-8')
    assert "startsWith('/static/')" in source                            # only static assets are cached
    assert "req.method !== 'GET'" in source                              # uploads/posts always go to the network


def test_offline_page_works_without_login(anon):
    res = anon.get('/offline')
    assert res.status_code == 200 and "You're offline" in res.get_data(as_text=True).replace('&#39;', "'")


# ---- pages advertise the app ----------------------------------------------------------------

def test_pages_link_the_manifest_icons_and_register_the_worker(client):
    html = client.get('/').get_data(as_text=True)
    assert 'rel="manifest"' in html and '/manifest.webmanifest' in html
    assert 'apple-touch-icon' in html and 'serviceWorker.register' in html
    assert 'theme-color' in html and 'viewport-fit=cover' in html


def test_dashboard_offers_install_instructions(client):
    html = client.get('/').get_data(as_text=True)
    assert 'id="installBanner"' in html and 'Add to Home screen' in html


# ---- launching from the home screen ------------------------------------------------------------

def test_signed_out_launch_logs_in_then_lands_on_the_scan_screen(A):
    fresh = A.app.test_client()
    fresh.post('/register', data={'username': 'carol', 'password': 'secret1'})
    start = fresh.get('/capture?source=pwa')
    assert start.status_code == 302 and '/login' in start.headers['Location']
    assert 'next=' in start.headers['Location']

    res = fresh.post(start.headers['Location'], data={'username': 'carol', 'password': 'secret1'})
    assert res.status_code == 302 and res.headers['Location'].startswith('/capture')


# ---- staying signed in ------------------------------------------------------------------------------

def login(A, remember):
    c = A.app.test_client()
    c.post('/register', data={'username': 'dave', 'password': 'secret1'})
    data = {'username': 'dave', 'password': 'secret1'}
    if remember:
        data['remember'] = 'y'
    res = c.post('/login', data=data)
    return c, res


def test_login_form_offers_keep_me_signed_in_checked_by_default(anon):
    html = anon.get('/login').get_data(as_text=True)
    assert 'Keep me signed in' in html and 'name="remember"' in html and 'checked' in html


def test_remember_cookie_is_set_and_outlives_the_session(A):
    _, res = login(A, remember=True)
    cookies = res.headers.getlist('Set-Cookie')
    remember = [c for c in cookies if c.startswith('remember_token=')]
    assert remember and 'Expires=' in remember[0] and 'HttpOnly' in remember[0] and 'SameSite=Lax' in remember[0]


def test_no_remember_cookie_when_unchecked(A):
    _, res = login(A, remember=False)
    assert not any(c.startswith('remember_token=') for c in res.headers.getlist('Set-Cookie'))


def test_remembered_login_survives_losing_the_session_cookie(A):
    client, _ = login(A, remember=True)
    client.delete_cookie('session')                                       # what closing the installed app does
    assert client.get('/expenses').status_code == 200                      # still signed in via the remember cookie
