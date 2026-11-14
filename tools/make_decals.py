"""Generate the decal atlas shipped with the add-on (robot_mech_animator/assets/decals.png + decals.json).

Run with a regular Python 3 that has Pillow:  python tools/make_decals.py
Fonts (Windows): STENCIL.TTF for military markings, Bahnschrift for technical labels.
Only the rasterised atlas is committed, not the fonts.
"""
import json
import math
import os
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "robot_mech_animator", "assets")
FONTS = "C:/Windows/Fonts"
W, H = 2048, 2048
SS = 2                      # supersampling

WHITE = (226, 224, 212, 255)
BLACK = (18, 18, 16, 255)
YELLOW = (226, 172, 28, 255)
RED = (168, 28, 22, 255)
OLIVE = (58, 62, 38, 255)
STEEL = (168, 170, 168, 255)


def font(name, size, variation=None):
    f = ImageFont.truetype(os.path.join(FONTS, name), size * SS)
    if variation:
        f.set_variation_by_name(variation)
    return f


def stencil(size):
    return font("STENCIL.TTF", size)


def bahn(size, var="Bold Condensed"):
    return font("bahnschrift.ttf", size, var)


def canvas(w, h):
    im = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    return im, ImageDraw.Draw(im)


def text_center(d, box, txt, f, fill):
    x0, y0, x1, y1 = [v * SS for v in box]
    bb = d.textbbox((0, 0), txt, font=f)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    d.text(((x0 + x1 - tw) / 2 - bb[0], (y0 + y1 - th) / 2 - bb[1]), txt, font=f, fill=fill)


def fit_font(d, txt, maker, w, h, start):
    size = start
    while size > 6:
        f = maker(size)
        bb = d.textbbox((0, 0), txt, font=f)
        if bb[2] - bb[0] <= w * SS and bb[3] - bb[1] <= h * SS:
            return f
        size -= 2
    return maker(size)


def rect(d, box, fill=None, outline=None, width=0, radius=0):
    b = [v * SS for v in box]
    d.rounded_rectangle(b, radius=radius * SS, fill=fill, outline=outline, width=width * SS)


def finish(im):
    return im.resize((im.width // SS, im.height // SS), Image.LANCZOS)


# --------------------------------------------------------------------------- decals
def d_number(txt):
    im, d = canvas(520, 320)
    text_center(d, (0, 0, 520, 320), txt, fit_font(d, txt, stencil, 500, 300, 300), WHITE)
    return finish(im)


def d_unit():
    im, d = canvas(760, 200)
    txt = "MK-VII"
    text_center(d, (0, 0, 760, 200), txt, fit_font(d, txt, stencil, 740, 180, 200), WHITE)
    return finish(im)


def d_hazard(w=760, h=150):
    im, d = canvas(w, h)
    d.rectangle([0, 0, w * SS, h * SS], fill=YELLOW)
    step = 90 * SS
    for k in range(-4, w * SS // step + 4):
        x = k * step
        d.polygon([(x, 0), (x + step // 2, 0), (x + step // 2 + h * SS, h * SS), (x + h * SS, h * SS)], fill=BLACK)
    return finish(im)


def d_danger():
    im, d = canvas(640, 300)
    rect(d, (4, 4, 636, 296), fill=YELLOW, outline=BLACK, width=6, radius=10)
    rect(d, (18, 18, 622, 104), fill=RED, radius=6)
    text_center(d, (18, 18, 622, 104), "DANGER", bahn(78), WHITE)
    text_center(d, (18, 112, 622, 196), "ROCKET EXHAUST AREA", fit_font(d, "ROCKET EXHAUST AREA", bahn, 590, 70, 70), BLACK)
    text_center(d, (18, 200, 622, 284), "KEEP CLEAR 50 M", fit_font(d, "KEEP CLEAR 50 M", bahn, 590, 64, 64), BLACK)
    return finish(im)


def d_nostep():
    im, d = canvas(520, 170)
    d.rectangle([0, 6 * SS, 520 * SS, 16 * SS], fill=WHITE)
    d.rectangle([0, 154 * SS, 520 * SS, 164 * SS], fill=WHITE)
    text_center(d, (0, 20, 520, 150), "NO STEP", fit_font(d, "NO STEP", stencil, 500, 120, 130), WHITE)
    return finish(im)


def d_hydraulic():
    im, d = canvas(640, 160)
    rect(d, (4, 4, 636, 156), fill=(28, 30, 26, 255), outline=WHITE, width=5, radius=8)
    text_center(d, (20, 10, 620, 92), "HYDRAULIC", fit_font(d, "HYDRAULIC", bahn, 580, 76, 80), WHITE)
    text_center(d, (20, 92, 620, 150), "3000 PSI  -  MIL-PRF-83282", fit_font(d, "3000 PSI  -  MIL-PRF-83282", lambda s: bahn(s, "SemiBold Condensed"), 580, 48, 50), YELLOW)
    return finish(im)


def d_dataplate():
    im, d = canvas(520, 330)
    rect(d, (4, 4, 516, 326), fill=STEEL, outline=(90, 92, 90, 255), width=4, radius=14)
    for x, y in ((24, 24), (496, 24), (24, 306), (496, 306)):
        d.ellipse([(x - 9) * SS, (y - 9) * SS, (x + 9) * SS, (y + 9) * SS], fill=(110, 112, 110, 255), outline=(60, 60, 60, 255), width=2 * SS)
    lines = [("RM-7 WALKER  ASSAULT FRAME", 40), ("SERIAL   2271-0815-B", 34), ("MFG  11/2026   LOT 44", 34),
             ("MASS  18 400 KG", 34), ("HYDR  3000 PSI   24 V DC", 34)]
    y = 40
    for txt, s in lines:
        f = bahn(s, "SemiBold Condensed")
        d.text((44 * SS, y * SS), txt, font=f, fill=(24, 24, 22, 255))
        y += s + 14
    rng = random.Random(7)            # barcode
    x = 44
    while x < 476:
        w = rng.choice((2, 2, 3, 4, 6))
        if rng.random() < 0.55:
            d.rectangle([x * SS, 268 * SS, (x + w) * SS, 306 * SS], fill=(24, 24, 22, 255))
        x += w + 1
    return finish(im)


def d_insignia():
    im, d = canvas(400, 400)
    c, r = 200 * SS, 186 * SS
    d.ellipse([c - r, c - r, c + r, c + r], fill=(30, 32, 26, 255))
    r2 = 160 * SS
    d.ellipse([c - r2, c - r2, c + r2, c + r2], fill=OLIVE)
    pts = []
    for k in range(10):                               # five-point star
        a = -math.pi / 2 + k * math.pi / 5
        rr = (118 if k % 2 == 0 else 48) * SS
        pts.append((c + rr * math.cos(a), c + 10 * SS + rr * math.sin(a)))
    d.polygon(pts, fill=(30, 32, 26, 255))
    for i in range(2):                                # two chevrons under the star
        y = c + (88 + i * 34) * SS
        d.line([(c - 70 * SS, y - 26 * SS), (c, y), (c + 70 * SS, y - 26 * SS)], fill=(30, 32, 26, 255), width=16 * SS)
    return finish(im)


def d_ammo():
    im, d = canvas(640, 120)
    rect(d, (0, 0, 640, 120), fill=YELLOW, radius=4)
    text_center(d, (0, 0, 640, 120), "AMMO  7.62 MM  LINKED", fit_font(d, "AMMO  7.62 MM  LINKED", bahn, 610, 92, 96), BLACK)
    return finish(im)


def d_warning():
    im, d = canvas(300, 270)
    tri = [(150 * SS, 8 * SS), (292 * SS, 262 * SS), (8 * SS, 262 * SS)]
    d.polygon(tri, fill=BLACK)
    inner = [(150 * SS, 40 * SS), (262 * SS, 244 * SS), (38 * SS, 244 * SS)]
    d.polygon(inner, fill=YELLOW)
    text_center(d, (100, 86, 200, 240), "!", bahn(150), BLACK)
    return finish(im)


def d_lift():
    im, d = canvas(300, 300)
    text_center(d, (0, 0, 300, 110), "LIFT", fit_font(d, "LIFT", stencil, 280, 100, 110), WHITE)
    d.polygon([(150 * SS, 290 * SS), (60 * SS, 150 * SS), (240 * SS, 150 * SS)], fill=WHITE)
    return finish(im)


def d_tag():
    im, d = canvas(320, 200)
    rect(d, (4, 4, 316, 196), fill=(236, 232, 214, 255), outline=BLACK, width=4, radius=6)
    rect(d, (4, 4, 316, 56), fill=(40, 90, 50, 255), radius=6)
    text_center(d, (4, 4, 316, 56), "INSPECTED", bahn(44), WHITE)
    text_center(d, (10, 64, 310, 128), "QC 09/26", fit_font(d, "QC 09/26", bahn, 290, 58, 60), BLACK)
    text_center(d, (10, 128, 310, 190), "TORQUE OK  #118", fit_font(d, "TORQUE OK  #118", lambda s: bahn(s, "SemiBold Condensed"), 290, 50, 50), BLACK)
    return finish(im)


def d_eject():
    im, d = canvas(520, 140)
    rect(d, (0, 0, 520, 140), fill=RED, radius=6)
    text_center(d, (0, 0, 520, 140), "RESCUE  ->  PULL", fit_font(d, "RESCUE  ->  PULL", bahn, 490, 104, 110), WHITE)
    return finish(im)


DECALS = [
    ("num_07", d_number("07")), ("num_12", d_number("12")), ("unit", d_unit()), ("hazard", d_hazard()),
    ("danger", d_danger()), ("nostep", d_nostep()), ("hydraulic", d_hydraulic()), ("dataplate", d_dataplate()),
    ("insignia", d_insignia()), ("ammo", d_ammo()), ("warning", d_warning()), ("lift", d_lift()),
    ("tag", d_tag()), ("rescue", d_eject()),
]


def pack():
    """Simple shelf packing; returns atlas and {name: [u0, v0, u1, v1]} (UV, origin bottom-left)."""
    atlas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    rects = {}
    x = y = shelf = 0
    pad = 16
    for name, im in sorted(DECALS, key=lambda t: -t[1].height):
        if x + im.width + pad > W:
            x, y, shelf = 0, y + shelf + pad, 0
        if y + im.height > H:
            raise RuntimeError("atlas too small")
        atlas.paste(im, (x, y))
        rects[name] = {"uv": [x / W, 1 - (y + im.height) / H, (x + im.width) / W, 1 - y / H],
                       "aspect": im.width / im.height}
        x += im.width + pad
        shelf = max(shelf, im.height)
    used = y + shelf
    return atlas, rects, used


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    atlas, rects, used = pack()
    # bleed the colours under transparent pixels (no dark fringes with mipmaps)
    rgb = atlas.convert("RGB").filter(ImageFilter.MaxFilter(5))
    bleed = Image.composite(atlas.convert("RGB"), rgb, atlas.getchannel("A"))
    bleed.putalpha(atlas.getchannel("A"))
    bleed.save(os.path.join(OUT, "decals.png"), optimize=True)
    with open(os.path.join(OUT, "decals.json"), "w", encoding="utf-8") as fh:
        json.dump(rects, fh, indent=1)
    print("atlas", W, H, "used height", used, "decals", len(rects))
