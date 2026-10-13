"""Mockup templates: a folder with background.jpg, template.json and optional overlay.png.

template.json:
{
  "name": "Framed print",
  "slots": [ {"corners": [[x,y],[x,y],[x,y],[x,y]]} ],   # TL, TR, BR, BL in background pixels
  "blend": {"mode": "normal" | "multiply", "opacity": 1.0, "fit": "cover" | "contain"},
  "overlay": "overlay.png"   # optional, multiply-blended on top (alpha respected)
}
Multiple slots are filled with consecutive photos (gallery walls, 3x3 grids).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from ..config import MOCKUPS_DIR

BG_NAME = "background.jpg"
TPL_NAME = "template.json"


def list_templates() -> list[dict]:
    out = []
    for d in sorted(MOCKUPS_DIR.iterdir()):
        t = d / TPL_NAME
        if d.is_dir() and t.exists() and (d / BG_NAME).exists():
            try:
                rec = json.loads(t.read_text())
            except Exception:
                continue
            rec["id"] = d.name
            rec.setdefault("name", d.name)
            rec.setdefault("blend", {})
            out.append(rec)
    return out


def get_template(tid: str) -> dict | None:
    d = MOCKUPS_DIR / tid
    if not (d / TPL_NAME).exists():
        return None
    rec = json.loads((d / TPL_NAME).read_text())
    rec["id"] = tid
    rec.setdefault("blend", {})
    return rec


def save_template(tid: str, name: str, slots: list[dict], blend: dict | None = None, background: Path | None = None,
                  overlay: Path | None = None) -> dict:
    d = MOCKUPS_DIR / tid
    d.mkdir(parents=True, exist_ok=True)
    if background is not None and Path(background).resolve() != (d / BG_NAME).resolve():
        Image.open(background).convert("RGB").save(d / BG_NAME, quality=92)
    if overlay is not None:
        Image.open(overlay).convert("RGBA").save(d / "overlay.png")
    rec = {"name": name, "slots": slots, "blend": blend or {"mode": "normal", "opacity": 1.0, "fit": "cover"}}
    if (d / "overlay.png").exists():
        rec["overlay"] = "overlay.png"
    (d / TPL_NAME).write_text(json.dumps(rec, indent=1))
    return get_template(tid)


def _fit_crop(photo: np.ndarray, quad: np.ndarray, fit: str) -> np.ndarray:
    """Crop the photo to the slot's aspect ratio (cover) so it fills the quad."""
    if fit == "contain":
        return photo
    tl, tr, br, bl = quad
    w = (np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)) / 2
    h = (np.linalg.norm(bl - tl) + np.linalg.norm(br - tr)) / 2
    target = w / max(h, 1e-6)
    ph, pw = photo.shape[:2]
    cur = pw / ph
    if abs(cur - target) < 1e-3:
        return photo
    if cur > target:
        nw = int(round(ph * target))
        x0 = (pw - nw) // 2
        return photo[:, x0 : x0 + nw]
    nh = int(round(pw / target))
    y0 = (ph - nh) // 2
    return photo[y0 : y0 + nh]


def render_mockup(template: dict, photos_srgb8: list[np.ndarray]) -> np.ndarray:
    """Composite edited sRGB uint8 photos into the template. Returns sRGB uint8 RGB."""
    d = MOCKUPS_DIR / template["id"]
    bg = np.array(Image.open(d / BG_NAME).convert("RGB")).astype(np.float32) / 255.0
    H, W = bg.shape[:2]
    blend = template.get("blend", {})
    mode = blend.get("mode", "normal")
    opacity = float(blend.get("opacity", 1.0))
    fit = blend.get("fit", "cover")
    out = bg.copy()
    for i, slot in enumerate(template.get("slots", [])):
        photo = photos_srgb8[i % len(photos_srgb8)]
        quad = np.array(slot["corners"], dtype=np.float32)
        photo = _fit_crop(photo, quad, fit)
        ph, pw = photo.shape[:2]
        # Anti-aliased edges: render at the slot's size, with a 1px feathered alpha
        src = np.array([[0, 0], [pw, 0], [pw, ph], [0, ph]], dtype=np.float32)
        M = cv2.getPerspectiveTransform(src, quad)
        pf = photo.astype(np.float32) / 255.0
        warped = cv2.warpPerspective(pf, M, (W, H), flags=cv2.INTER_AREA if pw > W else cv2.INTER_LINEAR,
                                     borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
        alpha = cv2.warpPerspective(np.ones((ph, pw), np.float32), M, (W, H), flags=cv2.INTER_LINEAR,
                                    borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        alpha = cv2.GaussianBlur(alpha, (0, 0), 0.6) * opacity
        if mode == "multiply":
            layer = warped * out
        else:
            layer = warped
        out = out * (1 - alpha[..., None]) + layer * alpha[..., None]
    ov_name = template.get("overlay")
    if ov_name and (d / ov_name).exists():
        ov = np.array(Image.open(d / ov_name).convert("RGBA")).astype(np.float32) / 255.0
        if ov.shape[:2] != (H, W):
            ov = cv2.resize(ov, (W, H), interpolation=cv2.INTER_AREA)
        a = ov[..., 3:4]
        out = out * (1 - a) + (out * ov[..., :3]) * a  # multiply blend
    return (np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8)


# --------------------------------------------------------------------------- procedural templates
def _rect(x, y, w, h):
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def _wall(w, h, tone=(214, 208, 198), vignette=0.25) -> Image.Image:
    arr = np.zeros((h, w, 3), np.float32)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r = np.sqrt(((xx - w / 2) / w) ** 2 + ((yy - h * 0.4) / h) ** 2)
    shade = 1.0 - vignette * np.clip(r * 1.6, 0, 1) ** 1.5
    arr[:] = np.array(tone, np.float32) / 255 * shade[..., None]
    noise = np.random.default_rng(0).normal(0, 0.01, (h, w, 1)).astype(np.float32)
    return Image.fromarray((np.clip(arr + noise, 0, 1) * 255).astype(np.uint8))


def _shadow_overlay(w, h, rects: list[tuple[int, int, int, int]], blur=18, offset=(0, 10), strength=0.55) -> Image.Image:
    """Multiply overlay with soft drop shadows under each rect (RGBA, white elsewhere)."""
    base = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(base)
    for x, y, rw, rh in rects:
        d.rectangle([x + offset[0], y + offset[1], x + rw + offset[0], y + rh + offset[1]], fill=int(255 * (1 - strength)))
    base = base.filter(ImageFilter.GaussianBlur(blur))
    for x, y, rw, rh in rects:  # keep the picture area itself clean
        d2 = ImageDraw.Draw(base)
        d2.rectangle([x, y, x + rw, y + rh], fill=255)
    rgba = Image.merge("RGBA", [base, base, base, Image.new("L", (w, h), 255)])
    return rgba


def _frame(img: Image.Image, x, y, w, h, border=26, mat=0, frame_color=(28, 26, 24), mat_color=(246, 244, 240)):
    d = ImageDraw.Draw(img)
    d.rectangle([x - border - mat, y - border - mat, x + w + border + mat, y + h + border + mat], fill=frame_color)
    if mat:
        d.rectangle([x - mat, y - mat, x + w + mat, y + h + mat], fill=mat_color)
    d.rectangle([x, y, x + w, y + h], fill=(90, 90, 90))


def _write(tid: str, name: str, bg: Image.Image, slots: list[list[list[int]]], overlay: Image.Image | None = None,
           blend: dict | None = None):
    d = MOCKUPS_DIR / tid
    d.mkdir(parents=True, exist_ok=True)
    bg.convert("RGB").save(d / BG_NAME, quality=92)
    rec = {"name": name, "slots": [{"corners": s} for s in slots], "blend": blend or {"mode": "normal", "opacity": 1.0, "fit": "cover"}}
    if overlay is not None:
        overlay.save(d / "overlay.png")
        rec["overlay"] = "overlay.png"
    (d / TPL_NAME).write_text(json.dumps(rec, indent=1))


def generate_placeholder_templates() -> list[str]:
    MOCKUPS_DIR.mkdir(exist_ok=True)
    ids = []
    W, H = 1600, 1200

    # 1. framed print on a wall
    bg = _wall(W, H)
    x, y, w, h = 400, 260, 800, 533
    _frame(bg, x, y, w, h, border=24, mat=50)
    _write("01-framed-print", "Framed print on wall", bg, [_rect(x, y, w, h)],
           _shadow_overlay(W, H, [(x - 74, y - 74, w + 148, h + 148)]))
    ids.append("01-framed-print")

    # 2. gallery wall of three
    bg = _wall(W, H, tone=(226, 224, 220))
    rects = [(120, 380, 400, 400), (600, 330, 400, 500), (1080, 380, 400, 400)]
    for (x, y, w, h) in rects:
        _frame(bg, x, y, w, h, border=16, mat=30, frame_color=(240, 238, 234))
    _write("02-gallery-wall", "Gallery wall of three", bg, [_rect(*r) for r in rects],
           _shadow_overlay(W, H, [(x - 46, y - 46, w + 92, h + 92) for x, y, w, h in rects], blur=14, strength=0.45))
    ids.append("02-gallery-wall")

    # 3. phone (9:16 screen), slightly rotated
    bg = _wall(W, H, tone=(60, 64, 70), vignette=0.5)
    d = ImageDraw.Draw(bg)
    cx, cy, pw, ph = 800, 600, 380, 780
    d.rounded_rectangle([cx - pw // 2 - 14, cy - ph // 2 - 14, cx + pw // 2 + 14, cy + ph // 2 + 14], radius=60, fill=(18, 18, 20))
    d.rounded_rectangle([cx - pw // 2, cy - ph // 2, cx + pw // 2, cy + ph // 2], radius=48, fill=(0, 0, 0))
    d.rounded_rectangle([cx - 60, cy - ph // 2 + 12, cx + 60, cy - ph // 2 + 40], radius=14, fill=(10, 10, 12))
    sw, sh = pw, int(pw * 16 / 9)
    sy = cy - sh // 2
    ov = Image.new("RGBA", (W, H), (255, 255, 255, 0))
    od = ImageDraw.Draw(ov)
    od.rounded_rectangle([cx - 60, sy + 12, cx + 60, sy + 40], radius=14, fill=(10, 10, 12, 255))  # notch above photo
    _write("03-phone", "Phone screen (9:16)", bg, [_rect(cx - sw // 2, sy, sw, sh)], ov)
    ids.append("03-phone")

    # 4. laptop, perspective screen
    bg = _wall(W, H, tone=(236, 232, 226), vignette=0.2)
    d = ImageDraw.Draw(bg)
    d.rectangle([0, 820, W, H], fill=(200, 196, 190))  # desk
    screen = [[330, 190], [1270, 190], [1300, 760], [300, 760]]
    lid = [[300, 160], [1300, 160], [1330, 790], [270, 790]]
    d.polygon([tuple(p) for p in lid], fill=(40, 40, 42))
    d.polygon([tuple(p) for p in screen], fill=(0, 0, 0))
    d.polygon([(220, 790), (1380, 790), (1440, 830), (160, 830)], fill=(190, 190, 192))
    _write("04-laptop", "Laptop screen", bg, [screen], _shadow_overlay(W, H, [(160, 800, 1280, 30)], blur=20))
    ids.append("04-laptop")

    # 5. instagram post (4:5) with UI chrome
    bg = Image.new("RGB", (1080, 1500), (255, 255, 255))
    d = ImageDraw.Draw(bg)
    d.ellipse([30, 30, 100, 100], fill=(230, 80, 120))
    d.rectangle([120, 50, 330, 80], fill=(40, 40, 40))
    d.rectangle([40, 1510 - 110, 90, 1510 - 60], fill=(60, 60, 60))
    d.rectangle([120, 1510 - 110, 170, 1510 - 60], fill=(60, 60, 60))
    d.rectangle([200, 1510 - 110, 250, 1510 - 60], fill=(60, 60, 60))
    d.rectangle([40, 1500 - 40, 400, 1500 - 20], fill=(200, 200, 200))
    _write("05-instagram-post", "Instagram post (4:5)", bg, [_rect(0, 130, 1080, 1350)])
    ids.append("05-instagram-post")

    # 6. instagram 3x3 grid
    bg = Image.new("RGB", (1080, 1500), (250, 250, 250))
    d = ImageDraw.Draw(bg)
    d.ellipse([60, 60, 240, 240], fill=(230, 80, 120))
    for i in range(3):
        d.rectangle([300 + i * 240, 110, 460 + i * 240, 135], fill=(50, 50, 50))
        d.rectangle([300 + i * 240, 160, 440 + i * 240, 180], fill=(150, 150, 150))
    d.rectangle([60, 300, 1020, 320], fill=(120, 120, 120))
    slots = []
    s = 356
    gap = 6
    for r in range(3):
        for c in range(3):
            slots.append(_rect(c * (s + gap), 420 + r * (s + gap), s, s))
    _write("06-instagram-grid", "Instagram 3x3 grid", bg, slots)
    ids.append("06-instagram-grid")

    # 7. poster leaning on a wall
    bg = _wall(W, H, tone=(200, 196, 190), vignette=0.35)
    d = ImageDraw.Draw(bg)
    d.rectangle([0, 1000, W, H], fill=(120, 110, 100))
    poster = [[520, 170], [1080, 150], [1090, 1000], [530, 1000]]
    d.polygon([tuple(p) for p in poster], fill=(250, 250, 250))
    inner = [[560, 210], [1040, 192], [1050, 960], [570, 960]]
    _write("07-poster", "Poster", bg, [inner], _shadow_overlay(W, H, [(520, 1000, 570, 24)], blur=16),
           blend={"mode": "normal", "opacity": 1.0, "fit": "cover"})
    ids.append("07-poster")

    # 8. magazine spread
    bg = _wall(W, H, tone=(232, 230, 226), vignette=0.3)
    d = ImageDraw.Draw(bg)
    left = [[150, 220], [795, 240], [795, 990], [150, 1010]]
    right = [[805, 240], [1450, 220], [1450, 1010], [805, 990]]
    d.polygon([tuple(p) for p in left], fill=(252, 252, 250))
    d.polygon([tuple(p) for p in right], fill=(255, 255, 254))
    d.rectangle([860, 300, 1380, 330], fill=(30, 30, 30))
    for i in range(14):
        d.rectangle([860, 380 + i * 36, 1380 - (i % 3) * 60, 392 + i * 36], fill=(160, 160, 160))
    photo = [[190, 260], [760, 278], [760, 950], [190, 970]]
    ov = Image.new("RGBA", (W, H), (255, 255, 255, 0))
    od = ImageDraw.Draw(ov)
    for i in range(40):  # gutter shading
        k = int(255 * (0.75 + 0.25 * i / 40))
        od.line([(795 - i, 240), (795 - i, 1000)], fill=(k, k, k, 255))
        od.line([(805 + i, 240), (805 + i, 1000)], fill=(k, k, k, 255))
    _write("08-magazine", "Magazine spread", bg, [photo], ov)
    ids.append("08-magazine")
    return ids


if __name__ == "__main__":
    print(generate_placeholder_templates())
