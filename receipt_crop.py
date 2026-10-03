"""Automatic receipt cropping.

Finds the receipt (a paper rectangle on a different-looking surface) in a photo, straightens its
perspective and trims the background. Detection works by proposing several candidate outlines and
keeping the one whose *border* is a long, consistent, straight edge - that is what separates a real
paper edge from a shape that happens to cut across a mottled table or through printed text. When
nothing convincing is found the photo is left untouched, so a bad detection can never make things
worse than "no crop".
"""
import logging
import os
import shutil
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Optional, Tuple

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
MOSTLY_FRAME_RATIO = 0.75    # a paper-like region this big is the receipt filling the frame: don't flag it as 'not found'
MIN_RECTANGULARITY = 0.82    # hull area / quad area; rejects blobs that aren't paper-shaped
MIN_CONTRAST = 18            # paper vs. surroundings, in gray levels
MIN_EDGE_STEP = 12           # every visible side must be a consistent step of at least this many gray levels
MIN_BORDER_SAMPLES = 30      # border points that are inside the frame (not running off its edge)
MAX_EXTENSION_SHIFT = 26     # how different a pixel in a paper strip added by an extension may look (shading allowed)
MIN_PAPER_FRACTION = 0.85    # ...and this share of the added strip must look like the paper
KMEANS_CLUSTERS = 4
PAD_RATIO = 0.012            # grow the quad a touch so edge text is never clipped
MIN_OUTPUT_SIDE = 150

# Quad corners are ordered tl(0), tr(1), br(2), bl(3); side i runs from corner i to corner i+1.
# For an end side: the (corner, anchor) pairs - each corner slides away from its anchor along the long side.
END_EXTENSION = {0: ((0, 3), (1, 2)), 2: ((3, 0), (2, 1)), 3: ((0, 1), (3, 2)), 1: ((1, 0), (2, 3))}


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


# ---- candidate regions -----------------------------------------------------------------

def _paper_mask(lab):
    """Pixels as bright and as colourless as the brightest part of the frame."""
    light = lab[:, :, 0]
    reference = np.median(lab[light >= np.percentile(light, 97)], axis=0)
    chroma = np.hypot(lab[:, :, 1].astype('float32') - reference[1], lab[:, :, 2].astype('float32') - reference[2])
    return ((light > reference[0] - 45) & (chroma < 16)).astype('uint8') * 255


def _cluster_masks(lab, k=KMEANS_CLUSTERS):
    """Split the frame into k colour groups; the paper is one of them (even when glare fools a single rule)."""
    h, w = lab.shape[:2]
    data = lab.reshape(-1, 3).astype('float32')
    sample = data[np.random.default_rng(0).choice(len(data), size=min(25000, len(data)), replace=False)]
    cv2.setRNGSeed(0)
    _, _, centers = cv2.kmeans(sample, k, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 15, 1.0),
                               1, cv2.KMEANS_PP_CENTERS)
    labels = ((data[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2).argmin(axis=1).reshape(h, w)
    for c in range(k):
        yield f'cluster{c}', (labels == c).astype('uint8') * 255


def _clean(mask, kernel):
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)


def _candidate_masks(ctx):
    """(name, mask, can_signal_full_frame): tight-tracing candidates, all judged by the same score."""
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, max(ctx.gray.shape) // 60),) * 2)
    yield 'paper', _clean(_paper_mask(ctx.lab), kernel), True
    _, otsu = cv2.threshold(ctx.blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    yield 'bright', _clean(otsu, kernel), True
    for name, mask in _cluster_masks(ctx.lab):
        yield name, _clean(mask, kernel), False


def _edge_mask(ctx):
    """Last resort: regions enclosed by edges (grows the outline slightly, so only used when nothing else works)."""
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, max(ctx.gray.shape) // 60),) * 2)
    edges = cv2.dilate(cv2.Canny(ctx.blur, 40, 130), kernel, iterations=1)
    return cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=3)


# ---- judging a candidate ---------------------------------------------------------------------

def _contrast_ok(gray, quad) -> bool:
    h, w = gray.shape
    inside = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(inside, np.round(quad).astype('int32'), 255)
    if int((inside == 0).sum()) < 0.02 * h * w:
        return False
    return float(gray[inside > 0].mean()) - float(gray[inside == 0].mean()) >= MIN_CONTRAST


def _gradients(blur):
    """Per-pixel brightness gradient (x, y), scaled so a value is roughly the size of a gray-level step."""
    return (cv2.Sobel(blur, cv2.CV_32F, 1, 0, ksize=3) / 4.0,
            cv2.Sobel(blur, cv2.CV_32F, 0, 1, ksize=3) / 4.0)


def _side_strengths(grads, quad):
    """Consistent gray-level step along each side of the outline; None where the side isn't visible.

    Along a real paper edge every point steps the same way (paper brighter than table), so the median of
    the step taken across the edge is large. Along a line that merely cuts through printed text or table
    texture the steps alternate and the median is near zero. Sides that run along the edge of the frame
    have no visible border.
    """
    gx, gy = grads
    h, w = gx.shape
    margin = 5
    centre = quad.mean(axis=0)
    offsets = np.arange(-4, 5)                      # tolerate an outline a few pixels off the true edge
    strengths = []
    for i in range(4):
        a, b = quad[i], quad[(i + 1) % 4]
        length = float(np.linalg.norm(b - a))
        if length < 1:
            strengths.append(None)
            continue
        tangent = (b - a) / length
        normal = np.array([-tangent[1], tangent[0]])
        if np.dot(centre - (a + b) / 2, normal) < 0:
            normal = -normal                         # point into the receipt
        pts = a + (b - a) * np.linspace(0, 1, max(20, int(length / 3)))[:, None]
        visible = (pts[:, 0] >= margin) & (pts[:, 0] < w - margin) & (pts[:, 1] >= margin) & (pts[:, 1] < h - margin)
        pts = pts[visible]
        if len(pts) < MIN_BORDER_SAMPLES // 2:
            strengths.append(None)
            continue
        probe = pts[:, None, :] + offsets[None, :, None] * normal[None, None, :]          # points x offsets x 2
        px = np.clip(np.round(probe[..., 0]).astype(int), 0, w - 1)
        py = np.clip(np.round(probe[..., 1]).astype(int), 0, h - 1)
        across = gx[py, px] * normal[0] + gy[py, px] * normal[1]                          # step across the edge
        strongest = across[np.arange(len(pts)), np.abs(across).argmax(axis=1)]           # signed, nearby maximum
        strengths.append(abs(float(np.median(strongest))))
    return strengths


def _line_through(a, b):
    d = b - a
    return a.astype('float64'), d / (np.linalg.norm(d) or 1.0)


def _intersect(l1, l2):
    (p, v), (q, u) = l1, l2
    cross = v[0] * u[1] - v[1] * u[0]
    if abs(cross) < 1e-6:
        return None                                       # parallel
    t = ((q[0] - p[0]) * u[1] - (q[1] - p[1]) * u[0]) / cross
    return p + v * t


def _refine_sides(quad, ctx, reach=10):
    """Snap each visible side onto the actual paper edge with a least-squares line through its edge pixels.

    The outline comes from a blob's hull, whose corners can be off by a lot when part of the paper is shaded.
    Fitting lines to the real edge points gives accurate sides (and so accurate corners and extensions).
    """
    gx, gy = ctx.grads
    h, w = gx.shape
    centre = quad.mean(axis=0)
    diagonal = float(np.hypot(h, w))
    offsets = np.arange(-reach, reach + 1)
    lines = []
    for i in range(4):
        a, b = quad[i], quad[(i + 1) % 4]
        length = float(np.linalg.norm(b - a))
        fallback = _line_through(a, b)
        if length < 1:
            lines.append(fallback)
            continue
        tangent = (b - a) / length
        normal = np.array([-tangent[1], tangent[0]])
        if np.dot(centre - (a + b) / 2, normal) < 0:
            normal = -normal
        pts = a + (b - a) * np.linspace(0, 1, max(30, int(length / 2)))[:, None]
        pts = pts[(pts[:, 0] >= 5) & (pts[:, 0] < w - 5) & (pts[:, 1] >= 5) & (pts[:, 1] < h - 5)]
        if len(pts) < 15:
            lines.append(fallback)                        # side runs along the frame edge: keep it
            continue
        probe = pts[:, None, :] + offsets[None, :, None] * normal[None, None, :]
        px = np.clip(np.round(probe[..., 0]).astype(int), 0, w - 1)
        py = np.clip(np.round(probe[..., 1]).astype(int), 0, h - 1)
        across = gx[py, px] * normal[0] + gy[py, px] * normal[1]
        pick = np.abs(across).argmax(axis=1)
        strongest = across[np.arange(len(pts)), pick]
        sign = np.sign(np.median(strongest))
        good = strongest * sign >= MIN_EDGE_STEP / 2 if sign else np.zeros(len(pts), bool)
        if int(good.sum()) < 15:
            lines.append(fallback)
            continue
        edge_points = (pts[good] + offsets[pick[good]][:, None] * normal).astype('float32')
        vx, vy, x0, y0 = cv2.fitLine(edge_points, cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
        lines.append((np.array([x0, y0], dtype='float64'), np.array([vx, vy], dtype='float64')))

    refined = quad.copy()
    for k in range(4):
        corner = _intersect(lines[(k - 1) % 4], lines[k])
        if corner is None or np.linalg.norm(corner - quad[k]) > 0.12 * diagonal:
            return quad                                   # fit went somewhere implausible: trust the original
        refined[k] = corner
    return refined


def _weakest(strengths) -> Optional[float]:
    seen = [s for s in strengths if s is not None]
    return min(seen) if seen else None


def _ray_to_frame(p, d, shape) -> float:
    """Distance from p along unit vector d to the frame boundary."""
    h, w = shape
    limits = []
    for pos, step, size in ((p[0], d[0], w - 1), (p[1], d[1], h - 1)):
        if step > 1e-9:
            limits.append((size - pos) / step)
        elif step < -1e-9:
            limits.append(-pos / step)
    return max(0.0, min(limits)) if limits else 0.0


def _extend_weak_ends(quad, strengths, ctx):
    """Both long sides clearly are paper edges but an end isn't: does the paper simply run on to the frame?

    Shading often makes the far end of a long receipt look like a different colour, so only part of it is
    detected. Push each weak end out to the frame edge, and accept only if the strip we added still looks
    like the same paper and the extended outline has strong visible sides.
    """
    lengths = [float(np.linalg.norm(quad[(i + 1) % 4] - quad[i])) for i in range(4)]
    long_pair, end_pair = ((1, 3), (0, 2)) if lengths[1] + lengths[3] >= lengths[0] + lengths[2] else ((0, 2), (1, 3))
    if any(strengths[i] is None or strengths[i] < MIN_EDGE_STEP for i in long_pair):
        return None
    weak_ends = [i for i in end_pair if strengths[i] is not None and strengths[i] < MIN_EDGE_STEP]
    if not weak_ends:
        return None

    extended = quad.copy()
    for end in weak_ends:
        for corner, anchor in END_EXTENSION[end]:
            direction = quad[corner] - quad[anchor]
            direction = direction / (np.linalg.norm(direction) or 1.0)
            extended[corner] = quad[corner] + direction * _ray_to_frame(quad[corner], direction, ctx.gray.shape)

    inside = np.zeros(ctx.gray.shape, np.uint8)
    cv2.fillConvexPoly(inside, np.round(quad).astype('int32'), 255)
    added = np.zeros(ctx.gray.shape, np.uint8)
    cv2.fillConvexPoly(added, np.round(extended).astype('int32'), 255)
    added[inside > 0] = 0
    if int((added > 0).sum()) < 25:
        return None
    reference = np.median(ctx.smooth_lab[inside > 0].astype('float32'), axis=0)
    shift = ctx.smooth_lab[added > 0].astype('float32') - reference
    distance = np.hypot(shift[:, 0] * 0.35, np.hypot(shift[:, 1], shift[:, 2]))      # colour matters more than shading
    if float((distance <= MAX_EXTENSION_SHIFT).mean()) < MIN_PAPER_FRACTION:
        return None

    new_strengths = _side_strengths(ctx.grads, extended)
    weakest = _weakest(new_strengths)
    if weakest is None or weakest < MIN_EDGE_STEP or not _contrast_ok(ctx.gray, extended):
        return None
    return extended, weakest


def _best_in_mask(mask, ctx):
    """Best-scoring plausible outline among the largest regions of one mask."""
    best, best_score, best_ratio, saw_full, best_extended = None, 0.0, 0.0, False, True
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:3]:
        hull = cv2.convexHull(contour)
        hull_area = cv2.contourArea(hull)
        ratio = hull_area / ctx.area
        if ratio < MIN_AREA_RATIO:
            continue
        if ratio > MOSTLY_FRAME_RATIO:
            saw_full = True
        if ratio > FULL_FRAME_RATIO:
            continue
        quad = order_points(_quad_from_hull(hull))
        quad_area = cv2.contourArea(quad)
        if quad_area <= 0:
            continue
        rectangularity = min(hull_area, quad_area) / max(hull_area, quad_area)
        if rectangularity < MIN_RECTANGULARITY or not _contrast_ok(ctx.gray, quad):
            continue

        quad = _refine_sides(quad, ctx)
        strengths = _side_strengths(ctx.grads, quad)
        strength = _weakest(strengths)
        if strength is None:
            continue
        extended = False
        if strength < MIN_EDGE_STEP:
            result = _extend_weak_ends(quad, strengths, ctx)
            if result is None:
                continue
            quad, strength = result
            ratio, extended = cv2.contourArea(quad) / ctx.area, True
        score = strength * rectangularity
        if (not extended, score) > (not best_extended, best_score):       # verified outlines beat extended ones
            best, best_score, best_ratio, best_extended = quad, score, ratio, extended
    return best, best_score, best_ratio, saw_full, best_extended


def find_receipt_quad(bgr) -> Tuple[Optional['np.ndarray'], str, float]:
    """Locate the receipt. Returns (quad in full-resolution coords | None, reason, area_ratio)."""
    full_h, full_w = bgr.shape[:2]
    scale = DETECT_SIZE / max(full_h, full_w)
    small = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else bgr
    scale = small.shape[1] / full_w
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    ctx = SimpleNamespace(gray=gray, blur=blur, lab=cv2.cvtColor(small, cv2.COLOR_BGR2LAB), grads=_gradients(blur),
                          smooth_lab=cv2.cvtColor(cv2.medianBlur(small, 7), cv2.COLOR_BGR2LAB),   # printed text erased
                          area=float(gray.shape[0] * gray.shape[1]))

    best, best_score, best_ratio, best_extended, saw_full_frame = None, 0.0, 0.0, True, False
    for name, mask, full_frame_signal in _candidate_masks(ctx):
        quad, score, ratio, saw_full, extended = _best_in_mask(mask, ctx)
        saw_full_frame = saw_full_frame or (saw_full and full_frame_signal)
        if quad is not None and (not extended, score) > (not best_extended, best_score):
            best, best_score, best_ratio, best_extended = quad, score, ratio, extended
            logger.debug("crop candidate %s: score %.1f, covers %.0f%%, extended=%s", name, score, ratio * 100, extended)

    if best is None:                                  # nothing convincing: try the looser edge-based outline
        quad, _, ratio, _, _ = _best_in_mask(_edge_mask(ctx), ctx)
        if quad is not None:
            best, best_ratio = quad, ratio

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


def _keep_original(path: str, originals_dir: Optional[str]):
    """Copy the untouched photo aside before it is overwritten, so a bad crop can be diagnosed or undone."""
    if not originals_dir:
        return
    try:
        os.makedirs(originals_dir, exist_ok=True)
        shutil.copy2(path, os.path.join(originals_dir, os.path.basename(path)))
    except OSError as exc:
        logger.warning("Could not keep original of %s: %s", path, exc)


def auto_crop_file(path: str, jpeg_quality: int = 90, originals_dir: Optional[str] = None) -> CropResult:
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
        _keep_original(path, originals_dir)
        params = [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality] if path.lower().endswith(('.jpg', '.jpeg')) else []
        if not cv2.imwrite(path, warped, params):
            return CropResult(False, 'error')
        logger.info("Auto-cropped %s (receipt covered %.0f%% of frame)", path, ratio * 100)
        return CropResult(True, 'cropped', ratio)
    except Exception as exc:                          # never let cropping break an upload
        logger.warning("Auto-crop failed for %s: %s", path, exc)
        return CropResult(False, 'error')
