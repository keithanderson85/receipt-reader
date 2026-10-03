"""Auto-crop, using synthetic 'photos' of a receipt on a table."""
import io

import cv2
import numpy as np
import pytest
from PIL import Image

import receipt_crop
from receipt_crop import CropResult, auto_crop_file, order_points

W, H = 1600, 1200


def receipt_texture(w=600, h=1400):
    tex = np.full((h, w, 3), 250, np.uint8)
    for i, y in enumerate(range(60, h - 40, 52)):
        cv2.putText(tex, f'ITEM {i:03d} .......... {i * 1.37:6.2f}', (30, y), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (20, 20, 20), 2)
    return tex


def photo(quad, bg=60, paper=None, size=(W, H), seed=1):
    """Paste the receipt texture onto a noisy background at the given destination quad."""
    rng = np.random.default_rng(seed)
    w, h = size
    img = np.clip(rng.normal(bg, 8, (h, w, 3)), 0, 255).astype(np.uint8)
    tex = receipt_texture() if paper is None else paper
    th, tw = tex.shape[:2]
    src = np.array([[0, 0], [tw - 1, 0], [tw - 1, th - 1], [0, th - 1]], np.float32)
    m = cv2.getPerspectiveTransform(src, np.asarray(quad, np.float32))
    warped = cv2.warpPerspective(tex, m, (w, h))
    mask = cv2.warpPerspective(np.full((th, tw), 255, np.uint8), m, (w, h)) > 0
    img[mask] = warped[mask]
    return img


def save(tmp_path, img, name='p.jpg'):
    path = str(tmp_path / name)
    assert cv2.imwrite(path, img)
    return path


def dims(path):
    with Image.open(path) as im:
        return im.size


def border_is_paper(path, max_margin=0.04):
    """After a good crop, bright paper starts within a few percent of every edge (no table left)."""
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    h, w = img.shape

    def margin(line):
        hits = np.nonzero(line > 150)[0]
        return hits[0] if len(hits) else len(line)

    left = np.median([margin(img[y, :]) for y in range(h // 4, 3 * h // 4, 25)])
    right = np.median([margin(img[y, ::-1]) for y in range(h // 4, 3 * h // 4, 25)])
    top = np.median([margin(img[:, x]) for x in range(w // 4, 3 * w // 4, 25)])
    bottom = np.median([margin(img[::-1, x]) for x in range(w // 4, 3 * w // 4, 25)])
    return max(left, right) / w < max_margin and max(top, bottom) / h < max_margin


# ---- successful crops ---------------------------------------------------------------

def test_upright_receipt_is_cropped_to_its_shape(tmp_path):
    path = save(tmp_path, photo([[500, 100], [1100, 100], [1100, 1100], [500, 1100]]))
    res = auto_crop_file(path)
    w, h = dims(path)
    assert res.cropped and res.reason == 'cropped'
    assert abs(w / h - 600 / 1000) < 0.08          # the receipt's own aspect ratio
    assert w < W * 0.5 and border_is_paper(path)   # background trimmed away


def test_rotated_and_skewed_receipt_is_straightened(tmp_path):
    quad = [[420, 160], [1010, 90], [1190, 1060], [560, 1130]]      # rotated + perspective
    path = save(tmp_path, photo(quad))
    res = auto_crop_file(path)
    w, h = dims(path)
    assert res.cropped
    assert h > w and border_is_paper(path)
    assert 0.45 < w / h < 0.85


def test_tall_thin_receipt_strip_is_cropped(tmp_path):
    path = save(tmp_path, photo([[650, 40], [950, 40], [950, 1160], [650, 1160]]))
    res = auto_crop_file(path)
    w, h = dims(path)
    assert res.cropped and w < 400 and h > 900 and border_is_paper(path)


def test_receipt_running_off_the_frame_is_trimmed_at_the_sides(tmp_path):
    # long receipt: top and bottom leave the frame, only the left/right edges are visible
    path = save(tmp_path, photo([[450, -300], [1150, -300], [1150, 1500], [450, 1500]]))
    res = auto_crop_file(path)
    w, h = dims(path)
    assert res.cropped and w < 900 and h > 1000


def test_png_input_stays_png(tmp_path):
    path = save(tmp_path, photo([[500, 100], [1100, 100], [1100, 1100], [500, 1100]]), 'p.png')
    assert auto_crop_file(path).cropped
    with Image.open(path) as im:
        assert im.format == 'PNG'


def test_light_background_with_enough_contrast(tmp_path):
    path = save(tmp_path, photo([[500, 100], [1100, 100], [1100, 1100], [500, 1100]], bg=150))
    assert auto_crop_file(path).cropped


def mottled_tile(w, h, seed=7):
    """Light tan tile with darker veins and lighter patches: bright overall, but tinted."""
    rng = np.random.default_rng(seed)
    base = np.zeros((h, w, 3), np.float32)
    base[:] = (125, 160, 190)                                    # BGR tan
    blotch = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 25)
    blotch = blotch / blotch.std() * 28
    base += blotch[:, :, None]
    speck = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 4)
    base[speck > 1.6] = (110, 115, 120)                          # grey flecks
    return np.clip(base + rng.normal(0, 6, (h, w, 3)), 0, 255).astype(np.uint8)


def test_long_receipt_on_a_light_tinted_tile_touching_three_edges(tmp_path):
    """Regression: a tall strip running off the top, bottom and right of the frame, on a light tan tile.
    Brightness alone sees the whole frame as 'paper'; the paper colour has to be used."""
    w, h = 1000, 2000
    img = mottled_tile(w, h)
    paper = receipt_texture(w, h)
    mask = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(mask, np.array([[560, 0], [w, 0], [w, h], [575, h]], np.int32), 255)
    img[mask > 0] = paper[mask > 0]
    path = save(tmp_path, img)

    res = auto_crop_file(path)
    out_w, out_h = dims(path)
    assert res.cropped, res
    assert out_w < 0.55 * w and out_h > 0.9 * h                  # tile on the left trimmed, full length kept
    assert out_w / out_h < 0.35


def paper_with_text(w, h, tint=(250, 250, 250)):
    paper = np.zeros((h, w, 3), np.uint8)
    paper[:] = tint
    for i, y in enumerate(range(60, h - 40, 52)):
        cv2.putText(paper, f'ITEM {i:03d} .......... {i * 1.37:6.2f}', (30, y), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (20, 20, 20), 2)
    return paper


def test_long_receipt_whose_far_end_is_shaded_is_cropped_to_full_length(tmp_path):
    """Regression: shading darkens the lower part of a long receipt so it looks like a different colour.
    Only the lit top used to be detected (or the crop was skipped)."""
    w, h = 1000, 2000
    img = mottled_tile(w, h)
    paper = paper_with_text(w, h)
    shade = np.linspace(1.0, 0.72, h)[None, :, None, None].reshape(h, 1, 1)            # darker towards the bottom
    paper = np.clip(paper.astype(np.float32) * shade, 0, 255).astype(np.uint8)
    mask = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(mask, np.array([[300, 120], [700, 118], [706, h], [296, h]], np.int32), 255)
    img[mask > 0] = paper[mask > 0]
    path = save(tmp_path, img)

    res = auto_crop_file(path)
    out_w, out_h = dims(path)
    assert res.cropped, res
    assert out_h > 0.9 * (h - 120) and out_w < 0.55 * w          # full length kept, tile trimmed at the sides


def test_extension_does_not_run_past_the_end_of_a_receipt(tmp_path):
    """Regression: a receipt that ends inside the frame must not be stretched down over the table below it."""
    w, h = 1000, 1800
    img = np.clip(np.random.default_rng(3).normal(55, 6, (h, w, 3)), 0, 255).astype(np.uint8)    # dark fabric
    paper = paper_with_text(420, 1100)
    img[200:1300, 290:710] = paper
    path = save(tmp_path, img)

    res = auto_crop_file(path)
    out_w, out_h = dims(path)
    assert res.cropped and out_h < 1250 and out_w < 520          # ends where the paper ends, not at the frame


def test_receipt_filling_the_frame_is_not_cut_in_half(tmp_path):
    """Regression: a line cut through printed text has plenty of contrast, but it is not a paper edge."""
    w, h = 940, 2750
    img = mottled_tile(w, h)
    img[:, 18:922] = paper_with_text(904, h)                     # paper nearly fills the width, runs off top and bottom
    shade = np.linspace(1.0, 0.8, h).reshape(h, 1, 1)
    img = np.clip(img.astype(np.float32) * shade, 0, 255).astype(np.uint8)
    path = save(tmp_path, img)

    res = auto_crop_file(path)
    out_w, out_h = dims(path)
    assert (not res.cropped) or out_h >= 0.95 * h               # never loses the bottom of the receipt


def test_glare_in_a_corner_does_not_hide_the_receipt(tmp_path):
    """Regression: the brightest pixels were bright tile in the corner, so the paper colour was learned wrongly."""
    w, h = 1200, 1200
    img = mottled_tile(w, h).astype(np.float32)
    yy, xx = np.mgrid[0:h, 0:w]
    glare = np.clip(1 - np.hypot(xx, yy) / 500, 0, 1)[:, :, None] * 95                # sunlit corner, brighter than paper
    img = np.clip(img + glare, 0, 255).astype(np.uint8)
    paper = paper_with_text(420, 1000, tint=(235, 222, 205))                         # cool white (BGR)
    img[100:1100, 560:980] = paper
    path = save(tmp_path, img)

    res = auto_crop_file(path)
    out_w, out_h = dims(path)
    assert res.cropped and out_w < 600 and out_h > 900


def test_original_is_kept_when_cropping(tmp_path):
    path = save(tmp_path, photo([[500, 100], [1100, 100], [1100, 1100], [500, 1100]]))
    before = read_bytes(path)
    keep = tmp_path / 'originals'
    assert auto_crop_file(path, originals_dir=str(keep)).cropped
    assert (keep / 'p.jpg').read_bytes() == before               # untouched copy
    assert read_bytes(path) != before                            # file itself is the cropped version


def test_no_original_is_saved_when_nothing_was_cropped(tmp_path):
    path = save(tmp_path, np.full((H, W, 3), 90, np.uint8))
    keep = tmp_path / 'originals'
    assert not auto_crop_file(path, originals_dir=str(keep)).cropped
    assert not keep.exists()


# ---- cases that must be left alone ----------------------------------------------------

def read_bytes(p):
    with open(p, 'rb') as fh:
        return fh.read()


def test_already_tight_photo_is_untouched(tmp_path):
    path = save(tmp_path, receipt_texture(900, 1400))
    before = read_bytes(path)
    res = auto_crop_file(path)
    assert not res.cropped and res.reason == 'full_frame' and not res.missed
    assert read_bytes(path) == before


def test_low_contrast_white_on_white_is_untouched(tmp_path):
    paper = np.full((1400, 600, 3), 245, np.uint8)
    for y in range(60, 1360, 52):
        cv2.putText(paper, 'ITEM ........ 1.00', (30, y), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (30, 30, 30), 2)
    path = save(tmp_path, photo([[500, 100], [1100, 100], [1100, 1100], [500, 1100]], bg=238, paper=paper))
    before = read_bytes(path)
    res = auto_crop_file(path)
    assert not res.cropped and read_bytes(path) == before


def test_blank_image_is_untouched(tmp_path):
    path = save(tmp_path, np.full((H, W, 3), 90, np.uint8))
    before = read_bytes(path)
    assert not auto_crop_file(path).cropped and read_bytes(path) == before


def test_tiny_object_in_frame_is_not_mistaken_for_a_receipt(tmp_path):
    path = save(tmp_path, photo([[700, 500], [820, 500], [820, 640], [700, 640]]))
    before = read_bytes(path)
    assert not auto_crop_file(path).cropped and read_bytes(path) == before


def test_corrupt_file_does_not_raise(tmp_path):
    path = tmp_path / 'bad.jpg'
    path.write_bytes(b'this is not an image')
    res = auto_crop_file(str(path))
    assert not res.cropped and res.reason == 'error' and res.missed
    assert path.read_bytes() == b'this is not an image'


def test_unavailable_dependencies_skip_cleanly(tmp_path, monkeypatch):
    monkeypatch.setattr(receipt_crop, 'CROP_AVAILABLE', False)
    res = auto_crop_file(str(tmp_path / 'whatever.jpg'))
    assert res == CropResult(False, 'unavailable') and not res.missed


# ---- helpers ------------------------------------------------------------------------

def test_order_points_is_rotation_invariant():
    pts = [[10, 10], [110, 10], [110, 210], [10, 210]]
    for shuffled in ([pts[2], pts[0], pts[3], pts[1]], [pts[1], pts[3], pts[0], pts[2]]):
        assert order_points(shuffled).tolist() == [[10, 10], [110, 10], [110, 210], [10, 210]]


# ---- capture route integration ----------------------------------------------------------

def jpeg_bytes(img):
    ok, buf = cv2.imencode('.jpg', img)
    return buf.tobytes()


def post(client, data, **extra):
    return client.post('/capture/process', content_type='multipart/form-data',
                       data={'photos': [(io.BytesIO(b), n) for n, b in data], **extra})


def stored_size(A, res):
    import os
    name = res.get_json()['redirect_url'].rsplit('/', 1)[1]
    with Image.open(os.path.join(A.app.config['UPLOAD_FOLDER'], name)) as im:
        return im.size


@pytest.fixture
def table_photo():
    return jpeg_bytes(photo([[500, 100], [1100, 100], [1100, 1100], [500, 1100]]))


def test_capture_crops_by_default(A, client, ocr, table_photo):
    res = post(client, [('a.jpg', table_photo)])
    assert res.get_json()['success'] and stored_size(A, res)[0] < W * 0.5


def test_capture_can_skip_cropping(A, client, ocr, table_photo):
    res = post(client, [('a.jpg', table_photo)], autocrop='0')
    assert stored_size(A, res) == (W, H)


def test_duplicate_detection_uses_the_untouched_upload(A, client, ocr, table_photo):
    res = post(client, [('a.jpg', table_photo), ('b.jpg', table_photo)])
    html = client.get(res.get_json()['redirect_url']).get_data(as_text=True)
    assert 'exact copy of photo 1' in html and ocr.calls[0]['images'] == 1


def test_user_is_told_when_the_receipt_edges_were_not_found(A, client, ocr, table_photo, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, 'auto_crop_file', lambda path, **kw: CropResult(False, 'not_found'))
    res = post(client, [('a.jpg', table_photo)])
    html = client.get(res.get_json()['redirect_url']).get_data(as_text=True)
    assert "find the receipt edges in photo 1" in html


def test_a_cropping_crash_never_blocks_the_upload(A, client, ocr, table_photo, monkeypatch):
    import app as app_module

    def boom(path, **kw):
        raise RuntimeError('cv exploded')
    monkeypatch.setattr(app_module, 'auto_crop_file', boom)
    # auto_crop_file guards itself; if a bug ever slips through, the route must report it, not hang
    res = post(client, [('a.jpg', table_photo)])
    assert res.status_code in (200, 500) and res.get_json() is not None
