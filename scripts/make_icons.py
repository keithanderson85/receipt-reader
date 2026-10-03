"""Generate the PWA icons in static/icons (run: python scripts/make_icons.py)."""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / 'static' / 'icons'
SIZE = 1024                       # drawn large, then downsampled for smooth edges


def gradient(size, top=(5, 150, 105), bottom=(13, 148, 136)):
    img = Image.new('RGB', (size, size))
    px = img.load()
    for y in range(size):
        t = y / (size - 1)
        row = tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        for x in range(size):
            px[x, y] = row
    return img


def receipt_glyph(draw, box, ink=(15, 23, 42)):
    """A paper receipt with a zig-zag bottom edge and a few text lines."""
    x0, y0, x1, y1 = box
    w = x1 - x0
    teeth = 8
    tooth = w / teeth
    top = [(x0, y0), (x1, y0)]
    zig = [(x1 - i * tooth, y1 - (tooth * 0.55 if i % 2 == 0 else 0)) for i in range(teeth + 1)]
    draw.polygon(top + zig, fill=(255, 255, 255))
    pad = w * 0.14
    height = y1 - y0
    line_h = height * 0.05
    for pos, frac in zip((0.12, 0.23, 0.34, 0.45, 0.56), (0.62, 0.84, 0.74, 0.84, 0.5)):
        y = y0 + height * pos
        draw.rounded_rectangle((x0 + pad, y, x0 + pad + (w - 2 * pad) * frac, y + line_h), radius=line_h / 2, fill=ink)
    # total bar, kept clear of the torn bottom edge
    y = y0 + height * 0.70
    draw.rounded_rectangle((x0 + pad, y, x1 - pad, y + line_h * 1.8), radius=line_h * 0.9, fill=(5, 150, 105))


def render(size_px, inset, rounded):
    """inset: fraction of the canvas kept as padding around the glyph (maskable icons need a safe zone)."""
    canvas = gradient(SIZE)
    if rounded:
        mask = Image.new('L', (SIZE, SIZE), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=SIZE * 0.22, fill=255)
        bg = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
        bg.paste(canvas, (0, 0), mask)
        canvas = bg
    else:
        canvas = canvas.convert('RGBA')
    draw = ImageDraw.Draw(canvas)
    m = SIZE * inset
    gw = (SIZE - 2 * m) * 0.62
    gh = (SIZE - 2 * m) * 0.82
    cx, cy = SIZE / 2, SIZE / 2
    receipt_glyph(draw, (cx - gw / 2, cy - gh / 2, cx + gw / 2, cy + gh / 2))
    return canvas.resize((size_px, size_px), Image.LANCZOS)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    render(192, 0.16, True).save(OUT / 'icon-192.png')
    render(512, 0.16, True).save(OUT / 'icon-512.png')
    render(512, 0.26, False).save(OUT / 'icon-maskable-512.png')      # full-bleed, glyph inside the safe zone
    render(180, 0.16, False).convert('RGB').save(OUT / 'apple-touch-icon.png')
    render(32, 0.10, True).save(OUT / 'favicon-32.png')
    print('icons written to', OUT)


if __name__ == '__main__':
    main()
