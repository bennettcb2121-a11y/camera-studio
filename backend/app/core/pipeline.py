"""THE edit pipeline. Used for previews, exports, mockups, AI previews and video looks.

Input is float32 linear RGB (sRGB primaries), output is float32 linear RGB; callers
quantise to sRGB 8/16-bit only at the very end with `finalize`.

Order (fixed): white balance, exposure, highlights/shadows, whites/blacks, tone curve,
HSL/vibrance/saturation, LUT (strength), local contrast, denoise, sharpen, crop/rotate,
vignette.

HSL, LUT, clarity, denoise and sharpen are inherently display-referred, so the image
is sRGB-encoded once before that block and decoded once after it. Between stages the
image is always linear float32. Every stage is skipped when its settings are neutral,
and the heavy lifting goes through multithreaded OpenCV calls so a slider change on
a 2048px proxy stays well under 150 ms.
"""
from __future__ import annotations

import math
import threading
from functools import lru_cache

import cv2
import numpy as np

from . import color
from .luts import load_lut
from .settings_schema import ASPECT_PRESETS, EditSettings

_N = 65536
_IDENTITY_CURVE = ((0.0, 0.0), (1.0, 1.0))


# --------------------------------------------------------------------------- helpers
def _wb_multipliers(temp: float, tint: float) -> np.ndarray:
    k = temp / 100.0
    t = tint / 100.0
    m = np.array([1.0 + 0.45 * k, 1.0 - 0.25 * t, 1.0 - 0.45 * k], dtype=np.float32)
    m = np.maximum(m, 0.05)
    # keep mid-grey luminance constant so WB does not act like exposure
    return m / float(m @ color.LUMA)


def _curve_lut(points: list[list[float]]) -> np.ndarray:
    """Monotone cubic (Fritsch-Carlson) through the points, sampled at 65536."""
    pts = sorted((float(x), float(y)) for x, y in points)
    if len(pts) < 2:
        pts = [(0.0, 0.0), (1.0, 1.0)]
    xs = np.array([p[0] for p in pts], dtype=np.float64)
    ys = np.array([p[1] for p in pts], dtype=np.float64)
    xs, idx = np.unique(xs, return_index=True)
    ys = ys[idx]
    if xs[0] > 0:
        xs, ys = np.insert(xs, 0, 0.0), np.insert(ys, 0, ys[0])
    if xs[-1] < 1:
        xs, ys = np.append(xs, 1.0), np.append(ys, ys[-1])
    n = len(xs)
    t = np.linspace(0, 1, _N)
    if n == 2:
        y = ys[0] + (ys[1] - ys[0]) * (t - xs[0]) / max(xs[1] - xs[0], 1e-6)
        return np.clip(y, 0, 1).astype(np.float32)
    h = np.diff(xs)
    d = np.diff(ys) / np.maximum(h, 1e-9)
    m = np.zeros(n)
    m[0], m[-1] = d[0], d[-1]
    for i in range(1, n - 1):
        m[i] = 0.0 if d[i - 1] * d[i] <= 0 else (d[i - 1] + d[i]) / 2
    for i in range(n - 1):
        if d[i] == 0:
            m[i] = m[i + 1] = 0
        else:
            a, b = m[i] / d[i], m[i + 1] / d[i]
            s = a * a + b * b
            if s > 9:
                tau = 3 / math.sqrt(s)
                m[i], m[i + 1] = tau * a * d[i], tau * b * d[i]
    seg = np.clip(np.searchsorted(xs, t, side="right") - 1, 0, n - 2)
    x0, x1 = xs[seg], xs[seg + 1]
    hh = np.maximum(x1 - x0, 1e-9)
    u = (t - x0) / hh
    h00 = 2 * u**3 - 3 * u**2 + 1
    h10 = u**3 - 2 * u**2 + u
    h01 = -2 * u**3 + 3 * u**2
    h11 = u**3 - u**2
    y = h00 * ys[seg] + h10 * hh * m[seg] + h01 * ys[seg + 1] + h11 * hh * m[seg + 1]
    return np.clip(y, 0, 1).astype(np.float32)


@lru_cache(maxsize=256)
def _tone_row(contrast: float, whites: float, blacks: float, curve_key: tuple) -> np.ndarray:
    """One combined linear->linear 1-D LUT for whites, blacks, contrast and the curve."""
    e = color.ENCODE_LUT.astype(np.float64)  # linear ramp -> encoded
    e = e * (1.0 + 0.25 * whites / 100.0)
    b = 0.15 * blacks / 100.0
    e = e + b * (1.0 - e)
    c = contrast / 100.0
    if c != 0:
        k = 1.0 + 1.5 * abs(c)
        mid = 0.5
        if c > 0:
            e = mid + np.sign(e - mid) * np.abs(e - mid) ** (1 / k) * (0.5 ** (1 - 1 / k))
        else:
            e = mid + np.sign(e - mid) * np.abs(e - mid) ** k * (0.5 ** (1 - k))
    e = np.clip(e, 0, 1)
    if curve_key != _IDENTITY_CURVE:
        cl = _curve_lut([list(p) for p in curve_key])
        e = cl[np.clip(e * (_N - 1), 0, _N - 1).astype(np.int64)]
    lin = color.DECODE_LUT[np.clip(e * (_N - 1), 0, _N - 1).astype(np.int64)].astype(np.float32)
    return color.lut1d_from_table(lin)


_HUE_CENTERS = np.array([0, 30, 60, 120, 180, 240, 280, 320], dtype=np.float32)
_HUE_WIDTH = np.array([30, 30, 40, 60, 60, 50, 40, 40], dtype=np.float32)
_HUE_STEPS = 3600


@lru_cache(maxsize=64)
def _hsl_tables(hue: tuple, sat: tuple, lum: tuple) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """1-D tables over hue (0.1 degree steps): hue shift (deg), sat gain, lum gain."""
    h = np.linspace(0, 360, _HUE_STEPS, endpoint=False, dtype=np.float32)
    d = np.abs((h[:, None] - _HUE_CENTERS + 180.0) % 360.0 - 180.0)
    w = np.clip(1.0 - d / _HUE_WIDTH, 0, 1)
    w = w / np.maximum(w.sum(axis=1, keepdims=True), 1e-6)
    hue_shift = (w @ np.array(hue, np.float32)) * 0.3
    sat_gain = 1.0 + (w @ np.array(sat, np.float32)) / 100.0
    lum_gain = 1.0 + (w @ np.array(lum, np.float32)) / 100.0 * 0.5
    return hue_shift.astype(np.float32), sat_gain.astype(np.float32), lum_gain.astype(np.float32)


_vig_cache: dict[tuple, np.ndarray] = {}
_vig_lock = threading.Lock()


def _radial(shape: tuple) -> np.ndarray:
    with _vig_lock:
        r = _vig_cache.get(shape)
        if r is None:
            h, w = shape
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
            r = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) / math.sqrt(2)
            r = color.smoothstep(0.35, 1.0, r).astype(np.float32)
            if len(_vig_cache) > 8:
                _vig_cache.clear()
            _vig_cache[shape] = r
        return r


def _blur(img: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian blur; large radii are done on a downscaled copy (visually identical)."""
    if sigma <= 4:
        return cv2.GaussianBlur(img, (0, 0), sigma)
    f = int(min(8, sigma / 2))
    h, w = img.shape[:2]
    small = cv2.resize(img, (max(1, w // f), max(1, h // f)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), sigma / f)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


# --------------------------------------------------------------------------- linear stages
def stage_white_balance(img, s: EditSettings):
    if s.wb.temp == 0 and s.wb.tint == 0:
        return img
    return color.mul3(img, _wb_multipliers(s.wb.temp, s.wb.tint))


def stage_exposure(img, s: EditSettings):
    if s.exposure == 0:
        return img
    return cv2.multiply(img, float(2.0 ** s.exposure))


def stage_highlights_shadows(img, s: EditSettings):
    if s.highlights == 0 and s.shadows == 0:
        return img
    le = color.encode(color.luminance(img))
    ev = np.zeros_like(le)
    if s.highlights != 0:
        ev += color.smoothstep(0.45, 0.95, le) * np.float32(1.2 * s.highlights / 100.0)
    if s.shadows != 0:
        ev += (1.0 - color.smoothstep(0.08, 0.6, le)) * np.float32(1.2 * s.shadows / 100.0)
    return color.mul_mask(img, color.exp2(ev))


def stage_tone(img, s: EditSettings):
    """whites, blacks, contrast and tone curve in one interpolated lookup."""
    curve_key = tuple(tuple(float(v) for v in p) for p in s.curve)
    if s.contrast == 0 and s.whites == 0 and s.blacks == 0 and curve_key == _IDENTITY_CURVE:
        return img
    row = _tone_row(float(s.contrast), float(s.whites), float(s.blacks), curve_key)
    return color.apply_lut1d(img, row)


# --------------------------------------------------------------------------- display stages
def _hsl_active(s: EditSettings) -> bool:
    h = s.hsl
    return any(h.hue) or any(h.sat) or any(h.lum) or s.vibrance != 0 or s.saturation != 0


def stage_hsl_enc(enc, s: EditSettings):
    h = s.hsl
    hsv = cv2.cvtColor(enc, cv2.COLOR_RGB2HSV)  # H 0..360, S,V 0..1
    H, S, V = cv2.split(hsv)
    if any(h.hue) or any(h.sat) or any(h.lum):
        shift, sgain, lgain = _hsl_tables(tuple(h.hue), tuple(h.sat), tuple(h.lum))
        idx = cv2.min(cv2.multiply(H, 10.0, dtype=cv2.CV_16U), _HUE_STEPS - 1)
        if any(h.hue):
            H = np.mod(cv2.add(H, shift[idx]), 360.0).astype(np.float32)
        if any(h.sat):
            S = cv2.multiply(S, sgain[idx])
        if any(h.lum):
            V = cv2.multiply(V, lgain[idx])
    if s.vibrance != 0:
        v = s.vibrance / 100.0
        # S * (1 + v*(1-S))
        S = cv2.multiply(S, cv2.add(cv2.multiply(cv2.subtract(1.0, S), v), 1.0))
    if s.saturation != 0:
        S = cv2.multiply(S, float(1.0 + s.saturation / 100.0))
    hsv = cv2.merge([H, np.clip(S, 0, 1, out=S), np.clip(V, 0, 1, out=V)])
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)


def stage_lut_enc(enc, s: EditSettings):
    lut = load_lut(s.lut.name)
    if lut is None:
        return enc
    out = lut.apply(enc)
    k = float(np.clip(s.lut.strength, 0, 1))
    if k < 1:
        out = cv2.addWeighted(enc, 1 - k, out, k, 0.0)
    return out


def stage_clarity_enc(enc, s: EditSettings):
    lum = color.luminance(enc)
    sigma = max(1.0, 0.015 * enc.shape[1])
    blur = _blur(lum, sigma)
    amount = 0.8 * s.clarity / 100.0
    new = np.clip(cv2.addWeighted(lum, 1 + amount, blur, -amount, 0.0), 0.001, 1.0)
    ratio = cv2.divide(new, cv2.max(lum, 0.001))
    return np.clip(color.mul_mask(enc, ratio), 0, 1)


def stage_clahe_enc(enc, s: EditSettings):
    """CLAHE on luminance (the 'Auto' button's local-contrast tool), blended by amount."""
    amount = float(np.clip(s.clahe / 100.0, 0, 1))
    lab = cv2.cvtColor(enc, cv2.COLOR_RGB2LAB)
    L = lab[..., 0]
    L8 = np.clip(L * 255.0 / 100.0, 0, 255).astype(np.uint8)
    clahe = cv2.createCLAHE(clipLimit=1.0 + 3.0 * amount, tileGridSize=(8, 8))
    L2 = clahe.apply(L8).astype(np.float32) * (100.0 / 255.0)
    lab[..., 0] = L * (1 - amount) + L2 * amount
    return np.clip(cv2.cvtColor(lab, cv2.COLOR_LAB2RGB), 0, 1)


def stage_denoise_enc(enc, s: EditSettings, scale: float):
    a = s.denoise / 100.0
    ycc = cv2.cvtColor(enc, cv2.COLOR_RGB2YCrCb)
    y, cr, cb = cv2.split(ycc)
    y = cv2.bilateralFilter(y, d=5, sigmaColor=0.02 + 0.12 * a, sigmaSpace=2.0 + 3.0 * a)
    cs = max(0.5, (1.0 + 6.0 * a) * max(scale, 0.25))
    cr = cv2.GaussianBlur(cr, (0, 0), cs)
    cb = cv2.GaussianBlur(cb, (0, 0), cs)
    return np.clip(cv2.cvtColor(cv2.merge([y, cr, cb]), cv2.COLOR_YCrCb2RGB), 0, 1)


def stage_sharpen_enc(enc, s: EditSettings, scale: float):
    sigma = max(0.6, 1.2 * scale)
    blur = cv2.GaussianBlur(enc, (0, 0), sigma)
    amount = 1.2 * s.sharpen / 100.0
    return np.clip(cv2.addWeighted(enc, 1 + amount, blur, -amount, 0.0), 0, 1)


# --------------------------------------------------------------------------- geometry
def stage_crop_rotate(img, s: EditSettings):
    c = s.crop
    if c.rotate != 0:
        h, w = img.shape[:2]
        M = cv2.getRotationMatrix2D((w / 2, h / 2), -c.rotate, 1.0)
        img = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    if (c.x, c.y, c.w, c.h) != (0.0, 0.0, 1.0, 1.0):
        h, w = img.shape[:2]
        x0 = int(round(np.clip(c.x, 0, 1) * w))
        y0 = int(round(np.clip(c.y, 0, 1) * h))
        x1 = int(round(np.clip(c.x + c.w, 0, 1) * w))
        y1 = int(round(np.clip(c.y + c.h, 0, 1) * h))
        if x1 - x0 >= 2 and y1 - y0 >= 2:
            img = np.ascontiguousarray(img[y0:y1, x0:x1])
    return img


def stage_vignette(img, s: EditSettings):
    if s.vignette == 0:
        return img
    # Lightroom convention: negative darkens the edges, positive lightens them.
    mask = _radial(img.shape[:2])
    v = s.vignette / 100.0
    ev = cv2.multiply(mask, float(1.5 * v if v < 0 else 1.0 * v))
    return color.mul_mask(img, color.exp2(ev))


# --------------------------------------------------------------------------- public
def render(img: np.ndarray, s: EditSettings, scale: float = 1.0) -> np.ndarray:
    """Run the pipeline. `scale` = this image's width / full-resolution width (for
    resolution-dependent radii). Returns float32 linear RGB, unclipped."""
    img = np.ascontiguousarray(img, dtype=np.float32)
    img = stage_white_balance(img, s)
    img = stage_exposure(img, s)
    img = stage_highlights_shadows(img, s)
    img = stage_tone(img, s)

    do_hsl = _hsl_active(s)
    do_lut = bool(s.lut.name) and s.lut.strength > 0
    do_clar = s.clarity != 0
    do_clahe = s.clahe > 0
    do_den = s.denoise > 0
    do_sharp = s.sharpen > 0
    if do_hsl or do_lut or do_clar or do_clahe or do_den or do_sharp:
        enc = color.encode(img)
        if do_hsl:
            enc = stage_hsl_enc(enc, s)
        if do_lut:
            enc = stage_lut_enc(enc, s)
        if do_clar:
            enc = stage_clarity_enc(enc, s)
        if do_clahe:
            enc = stage_clahe_enc(enc, s)
        if do_den:
            enc = stage_denoise_enc(enc, s, scale)
        if do_sharp:
            enc = stage_sharpen_enc(enc, s, scale)
        img = color.decode(enc)

    img = stage_crop_rotate(img, s)
    img = stage_vignette(img, s)
    return img


def finalize(lin: np.ndarray, bits: int = 8) -> np.ndarray:
    """Linear float32 -> sRGB uint8 / uint16 (the only place we leave linear)."""
    return color.to_uint16(lin) if bits == 16 else color.to_uint8(lin)


def render_srgb8(img: np.ndarray, s: EditSettings, scale: float = 1.0) -> np.ndarray:
    return finalize(render(img, s, scale), 8)


def crop_rect_for_aspect(w: int, h: int, aspect: str) -> dict:
    """Largest centred crop (fractions) with the given aspect preset."""
    ratio = ASPECT_PRESETS.get(aspect)
    if ratio is None:
        return {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
    cur = w / h
    if cur > ratio:
        cw = ratio / cur
        return {"x": (1 - cw) / 2, "y": 0.0, "w": cw, "h": 1.0}
    ch = cur / ratio
    return {"x": 0.0, "y": (1 - ch) / 2, "w": 1.0, "h": ch}


def histogram(lin: np.ndarray, bins: int = 64) -> dict:
    """Histogram of the sRGB-encoded output plus stats for the UI and the AI."""
    enc8 = color.to_uint8(lin)
    out: dict = {}
    for i, ch in enumerate("rgb"):
        hist = cv2.calcHist([enc8], [i], None, [bins], [0, 256]).ravel()
        out[ch] = (hist / max(hist.max(), 1)).round(4).tolist()
    lum = color.luminance(enc8.astype(np.float32))
    lhist = np.histogram(lum, bins=bins, range=(0, 256))[0].astype(np.float32)
    out["lum"] = (lhist / max(lhist.max(), 1)).round(4).tolist()
    total = lum.size
    out["stats"] = {
        "mean": float(lum.mean() / 255),
        "median": float(np.median(lum) / 255),
        "p1": float(np.percentile(lum, 1) / 255),
        "p99": float(np.percentile(lum, 99) / 255),
        "clipped_black": float((enc8.max(axis=2) <= 2).sum() / total),
        "clipped_white": float((enc8.min(axis=2) >= 253).sum() / total),
    }
    return out
