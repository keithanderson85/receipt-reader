"""ReceiptOCRGenAI with the OpenAI call stubbed."""
import os

import pytest
from PIL import Image

from conftest import OcrStub, item, make_jpeg
from receipt_ocr_genai import ReceiptOCRGenAI


@pytest.fixture
def engine(monkeypatch):
    eng = ReceiptOCRGenAI(openai_api_key='sk-test')
    stub = OcrStub()
    monkeypatch.setattr(eng, 'call_openai_with_retry', stub)
    return eng, stub


def write(tmp_path, name, **kw):
    path = tmp_path / name
    path.write_bytes(make_jpeg(**kw))
    return str(path)


def test_stitch_photos_stacks_at_common_width(engine, tmp_path):
    eng, _ = engine
    a = write(tmp_path, 'a.jpg', size=(700, 1000))
    b = write(tmp_path, 'b.jpg', size=(350, 500))
    out = eng.stitch_photos([a, b], str(tmp_path / 'out.jpg'), target_width=1400)
    with Image.open(out) as im:
        assert im.width == 1400
        assert im.height == 2000 + 2000          # 700x1000 and 350x500 both scale to 1400x2000


def test_multi_photo_receipt_is_read_together_and_merged(engine, tmp_path):
    eng, stub = engine
    p1, p2, p3 = (write(tmp_path, f'p{i}.jpg', label=str(i)) for i in (1, 2, 3))
    stub.response['items'] = [item('MILK', 3, 1), item('EGGS', 2, 1), item('EGGS', 2, 2), item('BREAD', 5, 2)]
    stub.response['subtotal'] = 10

    res = eng.process_receipt(p1, photo_paths=[p1, p2, p3])

    assert res['success'] and res['page_count'] == 3 and res['is_multipage']
    assert [i['description'] for i in res['items']] == ['MILK', 'EGGS', 'BREAD']
    assert any('overlap' in n['message'] for n in res['photo_notes'])
    assert res['method'] == 'gpt_vision_photos_3p'
    assert stub.calls[0]['images'] == 3 and 'MULTI-PHOTO' in stub.calls[0]['prompt']
    # stitched image kept for the expense, the individual photos cleaned up
    assert res['processed_filename'].endswith('_receipt.jpg')
    assert os.path.exists(tmp_path / res['processed_filename'])
    assert not any(os.path.exists(p) for p in (p1, p2, p3))


def test_multi_photo_warns_when_items_do_not_match_subtotal(engine, tmp_path):
    eng, stub = engine
    p1, p2 = write(tmp_path, 'a.jpg'), write(tmp_path, 'b.jpg', color='gray')
    stub.response['items'] = [item('A', 1, 1), item('B', 1, 2)]
    stub.response['subtotal'] = 50
    res = eng.process_receipt(p1, photo_paths=[p1, p2])
    assert any('subtotal' in n['message'] for n in res['photo_notes'])


def test_single_photo_path_is_unchanged(engine, tmp_path):
    eng, stub = engine
    p = write(tmp_path, 'single.jpg')
    stub.response['items'] = [item('A', 5)]
    res = eng.process_receipt(p)
    assert res['success'] and res['page_count'] == 1 and not res['is_multipage']
    assert 'MULTI-PHOTO' not in stub.calls[0]['prompt']
    assert res['photo_notes'] == []
    assert os.path.exists(p) and not res.get('processed_filename')


def test_model_failure_returns_fallback_result_not_exception(engine, tmp_path):
    eng, stub = engine
    p1, p2 = write(tmp_path, 'a.jpg'), write(tmp_path, 'b.jpg')
    stub.error = RuntimeError('boom')
    res = eng.process_receipt(p1, photo_paths=[p1, p2])
    assert res['success'] is False and 'boom' in res['error']


def test_invalid_json_from_model_is_a_clean_failure(engine, tmp_path, monkeypatch):
    eng, _ = engine
    monkeypatch.setattr(eng, 'call_openai_with_retry', lambda *a, **k: 'not json at all')
    res = eng.process_receipt(write(tmp_path, 'a.jpg'))
    assert res['success'] is False and 'JSON' in res['error']


def test_date_parsing(engine, tmp_path):
    eng, stub = engine
    stub.response['date'] = '10/02/2026'
    res = eng.process_receipt(write(tmp_path, 'a.jpg'))
    assert str(res['date']) == '2026-10-02'


def test_no_client_without_api_key():
    res = ReceiptOCRGenAI(openai_api_key=None).process_receipt('whatever.jpg')
    assert res['success'] is False
