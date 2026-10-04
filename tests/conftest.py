"""Shared fixtures.

The app uses relative paths (receipts.db, uploads/) and loads a real .env at import, so:
  * the environment is pinned *before* app is imported (load_dotenv never overrides it),
  * every test runs in its own temp working directory with a fresh database,
  * OpenAI is stubbed - no test can reach the network.
"""
import io
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.update(SECRET_KEY='test-secret', OPENAI_API_KEY='sk-test',
                  EBAY_DASHBOARD_URL='', EBAY_DASHBOARD_API_KEY='')


@pytest.fixture(scope='session')
def app_module(tmp_path_factory):
    os.chdir(tmp_path_factory.mktemp('import'))
    import app as A
    return A


@pytest.fixture
def A(app_module, tmp_path, monkeypatch):
    """The app module, pointed at an empty per-test workspace."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'uploads').mkdir()
    (tmp_path / 'reviews' / 'ocr').mkdir(parents=True)
    app_module.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SCANS_SYNC=True,      # readers run inline
                                 UPLOAD_FOLDER=str(tmp_path / 'uploads'))
    monkeypatch.setattr(app_module, 'BULK_REVIEW_FOLDER', str(tmp_path / 'reviews'))
    monkeypatch.setattr(app_module, 'OCR_CACHE_FOLDER', str(tmp_path / 'reviews' / 'ocr'))
    app_module.init_db()
    return app_module


class OcrStub:
    """Stands in for the OpenAI call; set .response (dict) or .error."""
    def __init__(self):
        self.response = {
            'merchant_name': 'Test Mart', 'address': '1 Main St, Reno, NV 89501', 'location': 'Reno, NV',
            'amount': 12.0, 'date': '2026-10-01', 'subtotal': 10.0, 'tax_amount': 2.0, 'tax_rate': 8,
            'discount_amount': 0, 'items': [],
        }
        self.error = None
        self.quick_response = None      # defaults to the merchant/amount/date of .response
        self.quick_error = None
        self.calls = []

    def __call__(self, messages, model=None, max_retries=3, max_tokens=2500):
        content = messages[0]['content']
        quick = 'Read ONLY these three things' in content[0]['text']
        self.calls.append({
            'prompt': content[0]['text'],
            'images': sum(1 for c in content if c['type'] == 'image_url'),
            'kind': 'quick' if quick else 'full',
        })
        if quick:
            if self.quick_error:
                raise self.quick_error
            if self.quick_response is not None:
                return self.quick_response if isinstance(self.quick_response, str) else json.dumps(self.quick_response)
            return json.dumps({k: self.response.get(k) for k in ('merchant_name', 'amount', 'date')})
        if self.error:
            raise self.error
        return json.dumps(self.response)


@pytest.fixture
def ocr(A, monkeypatch):
    stub = OcrStub()
    monkeypatch.setattr(A.receipt_ocr, 'call_openai_with_retry', stub)
    return stub


def _login(A, username):
    c = A.app.test_client()
    c.post('/register', data={'username': username, 'password': 'secret1'})
    c.post('/login', data={'username': username, 'password': 'secret1'})
    return c


@pytest.fixture
def client(A):
    return _login(A, 'alice')          # user id 1


@pytest.fixture
def other_client(A, client):
    return _login(A, 'bob')            # user id 2 (alice registered first)


@pytest.fixture
def anon(A):
    return A.app.test_client()


@pytest.fixture
def add_expense(A):
    def _add(user_id=1, merchant='Shell', amount=10.0, date='2026-10-01', category='travel',
             location='Reno, NV', address='1 Main St', items=(), **extra):
        cols = dict(user_id=user_id, merchant_name=merchant, amount=amount, date=date, category=category,
                    location=location, address=address, receipt_filename=None)
        cols.update(extra)
        con = sqlite3.connect('receipts.db')
        cur = con.execute(f"INSERT INTO expenses ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                          list(cols.values()))
        for desc, price, qty in items:
            con.execute('INSERT INTO receipt_items (expense_id, description, price, quantity) VALUES (?,?,?,?)',
                        (cur.lastrowid, desc, price, qty))
        con.commit()
        con.close()
        return cur.lastrowid
    return _add


@pytest.fixture
def add_location(A):
    def _add(name, address, user_id=1, codes='', category=None):
        con = sqlite3.connect('receipts.db')
        cur = con.execute('INSERT INTO locations (user_id, name, address, category, store_codes) VALUES (?, ?, ?, ?, ?)',
                          (user_id, name, address, category, codes))
        con.commit()
        con.close()
        return cur.lastrowid
    return _add


@pytest.fixture
def db():
    def _q(sql, *args):
        con = sqlite3.connect('receipts.db')
        try:
            return con.execute(sql, args).fetchall()
        finally:
            con.close()
    return _q


def make_jpeg(color='white', label='x', size=(600, 900)):
    im = Image.new('RGB', size, color)
    ImageDraw.Draw(im).text((20, 20), label, fill='black')
    buf = io.BytesIO()
    im.save(buf, 'JPEG')
    return buf.getvalue()


@pytest.fixture
def jpeg():
    return make_jpeg


def item(desc, price, photo=None, qty=1, sku=None):
    d = {'description': desc, 'price': price, 'quantity': qty, 'sku': sku}
    if photo is not None:
        d['photo'] = photo
    return d
