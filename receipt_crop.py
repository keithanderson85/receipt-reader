"""Automatic receipt cropping.

Finds the receipt (a bright paper rectangle on a darker surface) in a photo, straightens its
perspective and trims the background. When it isn't confident it leaves the photo untouched,
so a bad detection can never make things worse than "no crop".
"""
import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import cv2
    import numpy as np
    from PIL import Image, ImageOps
    CROP_AVAILABLE = True
except ImportError:                                   # opencv/numpy/pillow missing: cropping is skipped
    CROP_AVAILABLE = False

DETECT_SIZE = 900            # long side used for detection (full-resolution pixels are used for the warp)
MIN_AREA_RATIO = 0.12        # receipt must cover at least this much of the frame
FULL_FRAME_RATIO = 0.94      # ...and more than this means it already fills the frame: nothing to trim
MIN_RECTANGULARITY = 0.82    # hull area / quad area; rejects blobs that aren't paper-shaped
MIN_CONTRAST = 18            # paper vs. surroundings, in gray levels
PAD_RATIO = 0.012            # grow the quad a touch so edge text is never clipped
MIN_OUTPUT_SIDE = 150


@dataclass
class CropResult:
    cropped: bool
    reason: str               # cropped | full_frame | not_found | unavailable | error
    area_ratio: float = 0.0

    @property
    def missed(self) -> bool:
        """True when we looked for a receipt and could not find one (worth telling the user)."""
        return self.reason in ('not_found', 'error')


def order_points(pts):
    """Return the 4 points ordered top-left, top-right, bottom-right, bottom-left."""
    pts = np.asarray(pts, dtype='float32').reshape(4, 2)
    s, d = pts.sum(axis=1), np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], dtype='float32')


def _quad_from_hull(hull):
    peri = cv2.arcLength(hull, True)
    approx = cv2.approxPolyDP(hull, 0.02 * peri, True)
    if len(approx) == 4:
        return approx.reshape(4, 2).astype('float32')
    return cv2.boxPoints(cv2.minAreaRect(hull)).astype('float32')


def _paper_mask(small_bgr):
    """Pixels that look like the paper: as bright and as colourless as the brightest part of the frame.

    Beats plain brightness when the surface is light but tinted (tan tile, wood), because the paper is
    neutral white while the table is not.
    """
    lab = cv2.cvtColor(small_bgr, cv2.COLOR_BGR2LAB)
    light = lab[:, :, 0]
    reference = np.median(lab[light >= np.percentile(light, 97)], axis=0)
    chroma = np.hypot(lab[:, :, 1].astype('float32') - reference[1], lab[:, :, 2].astype('float32') - reference[2])
    mask = ((light > reference[0] - 45) & (chroma < 16)).astype('uint8') * 255
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))


def _masks(small_bgr, gray):
    """Candidate 'this is the receipt' masks, tightest-tracing first."""
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    side = max(gray.shape)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, side // 60),) * 2)

    yield 'paper', cv2.morphologyEx(_paper_mask(small_bgr), cv2.MORPH_CLOSE, kernel, iterations=2)

    _, bright = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    yield 'bright', cv2.morphologyEx(bright, cv2.MORPH_CLOSE, kernel, iterations=2)

    edges = cv2.Canny(blur, 40, 130)
    edges = cv2.dilate(edges, kernel, iterations=1)
    yield 'edges', cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=3)


def _score(gray, quad, area_ratio) -> Optional[float]:
    """Rectangularity x contrast x size, or None when the candidate is implausible."""
    h, w = gray.shape
    quad_area = cv2.contourArea(quad)
    if quad_area <= 0:
        return None
    inside = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(inside, np.round(quad).astype('int32'), 255)
    outside_px = int((inside == 0).sum())
    if outside_px < 0.02 * h * w:
        return None
    contrast = float(gray[inside > 0].mean()) - float(gray[inside == 0].mean())
    if contrast < MIN_CONTRAST:
        return None
    return area_ratio ** 0.5 * (contrast / (contrast + 30.0))


def find_receipt_quad(bgr) -> Tuple[Optional['np.ndarray'], str, float]:
    """Locate the receipt. Returns (quad in full-resolution coords | None, reason, area_ratio)."""
    full_h, full_w = bgr.shape[:2]
    scale = DETECT_SIZE / max(full_h, full_w)
    small = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else bgr
    scale = small.shape[1] / full_w
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    frame_area = float(gray.shape[0] * gray.shape[1])

    best, best_ratio, saw_full_frame = None, 0.0, False
    for name, mask in _masks(small, gray):                   # earlier masks trace the paper more tightly: first valid wins
        best_score = 0.0
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:3]:
            hull = cv2.convexHull(contour)
            hull_area = cv2.contourArea(hull)
            ratio = hull_area / frame_area
            if ratio < MIN_AREA_RATIO:
                continue
            if ratio > FULL_FRAME_RATIO:
                saw_full_frame = True
                continue
            quad = _quad_from_hull(hull)
            quad_area = cv2.contourArea(quad)
            if quad_area <= 0 or min(hull_area, quad_area) / max(hull_area, quad_area) < MIN_RECTANGULARITY:
                continue
            score = _score(gray, quad, ratio)
            if score is not None and score > best_score:
                best, best_score, best_ratio = quad, score, ratio
        if best is not None:
            break

    if best is None:
        return None, ('full_frame' if saw_full_frame else 'not_found'), 0.0
    return best / scale, 'cropped', best_ratio


def warp_quad(bgr, quad):
    """Perspective-correct the quad into an upright rectangle."""
    h, w = bgr.shape[:2]
    quad = order_points(quad)

    centre = quad.mean(axis=0)
    quad = centre + (quad - centre) * (1 + 2 * PAD_RATIO)
    quad[:, 0] = np.clip(quad[:, 0], 0, w - 1)
    quad[:, 1] = np.clip(quad[:, 1], 0, h - 1)

    tl, tr, br, bl = quad
    out_w = int(round(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl))))
    out_h = int(round(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr))))
    if min(out_w, out_h) < MIN_OUTPUT_SIDE:
        return None
    dst = np.array([[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]], dtype='float32')
    matrix = cv2.getPerspectiveTransform(quad, dst)
    return cv2.warpPerspective(bgr, matrix, (out_w, out_h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def _load_bgr(path):
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert('RGB')
        return cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)


def auto_crop_file(path: str, jpeg_quality: int = 90) -> CropResult:
    """Crop the receipt in ``path`` in place. The file is only rewritten when a crop is applied."""
    if not CROP_AVAILABLE:
        return CropResult(False, 'unavailable')
    try:
        bgr = _load_bgr(path)
        quad, reason, ratio = find_receipt_quad(bgr)
        if quad is None:
            return CropResult(False, reason)
        warped = warp_quad(bgr, quad)
        if warped is None:
            return CropResult(False, 'not_found')
        params = [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality] if path.lower().endswith(('.jpg', '.jpeg')) else []
        if not cv2.imwrite(path, warped, params):
            return CropResult(False, 'error')
        logger.info("Auto-cropped %s (receipt covered %.0f%% of frame)", path, ratio * 100)
        return CropResult(True, 'cropped', ratio)
    except Exception as exc:                          # never let cropping break an upload
        logger.warning("Auto-crop failed for %s: %s", path, exc)
        return CropResult(False, 'error')
