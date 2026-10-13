"""The "Auto" button: analyse the proxy and return slider values.

- white balance: gray-world estimate blended 50/50 with the camera WB (which the
  proxy already has applied, so "camera" == no change)
- exposure: histogram based, push the median towards mid-grey without clipping
- local contrast: CLAHE on luminance
- denoise: from ISO
- lens correction: on (applies only when lensfun knows the lens)
"""
from __future__ import annotations

import math

import numpy as np

from . import color
from .settings_schema import EditSettings


def gray_world_wb(lin: np.ndarray, blend: float = 0.5) -> tuple[float, float]:
    """Return (temp, tint) slider values that move the image towards neutral grey."""
    # use mid-tones only so a blue sky or a dark background does not dominate
    lum = color.luminance(lin)
    mask = (lum > 0.03) & (lum < 0.6)
    if mask.sum() < 1000:
        mask = np.ones_like(lum, dtype=bool)
    px = lin[mask]
    means = px.mean(axis=0) + 1e-6
    # Gray-world is unreliable when one colour dominates (blue sky, tungsten night):
    # damp the correction by the scene's average chroma.
    mx, mn = px.max(axis=1), px.min(axis=1)
    chroma = float(np.mean((mx - mn) / (mx + 1e-6)))
    blend *= float(np.clip(1.0 - (chroma - 0.15) / 0.35, 0.15, 1.0))
    r_over_b = means[0] / means[2]
    g_over_rb = means[1] / math.sqrt(means[0] * means[2])
    # Our WB stage: R *= 1+0.45k, B *= 1-0.45k, G *= 1-0.25t. Solve for the k, t
    # that would neutralise the measured cast, then blend towards the camera WB.
    # (1+0.45k)/(1-0.45k) = 1/r_over_b  ->  k = (1-r)/(0.45(1+r)), r = r_over_b
    k = (1.0 - r_over_b) / (0.45 * (1.0 + r_over_b))
    t = (1.0 - 1.0 / g_over_rb) / 0.25
    temp = float(np.clip(k * 100.0 * blend, -30, 30))
    tint = float(np.clip(t * 100.0 * blend, -20, 20))
    return round(temp, 1), round(tint, 1)


def auto_exposure(lin: np.ndarray) -> tuple[float, float, float]:
    """Return (exposure EV, highlights, shadows) from the luminance histogram."""
    lum = color.luminance(lin)
    med = float(np.median(lum)) + 1e-6
    p999 = float(np.percentile(lum, 99.7)) + 1e-6
    p02 = float(np.percentile(lum, 0.3))
    target = 0.16  # linear mid-grey-ish for a pleasant median
    ev = 0.7 * math.log2(target / med)
    # brighten freely, but only darken a bright scene if it is actually clipping
    clipped = float((lum >= 0.98).mean())
    lo = -1.0 if clipped > 0.02 else -0.3
    ev = float(np.clip(ev, lo, 1.2))
    # don't push the 99.7th percentile past white
    ev = min(ev, math.log2(0.95 / p999))
    highlights = 0.0
    shadows = 0.0
    if p999 * 2**ev > 0.8:
        highlights = -25.0
    if p02 * 2**ev < 0.004:
        shadows = 20.0
    return round(ev, 2), highlights, shadows


def iso_denoise(iso: int | None) -> float:
    if not iso or iso <= 400:
        return 0.0
    return float(np.clip(20.0 * math.log2(iso / 400.0), 0, 70))


def auto_settings(lin: np.ndarray, exif: dict | None, base: EditSettings | None = None) -> EditSettings:
    """Compute Auto adjustments relative to a neutral starting point (keeps crop/LUT)."""
    base = base or EditSettings()
    temp, tint = gray_world_wb(lin)
    ev, hl, sh = auto_exposure(lin)
    iso = (exif or {}).get("iso")
    partial = {
        "wb": {"temp": temp, "tint": tint},
        "exposure": ev,
        "highlights": hl,
        "shadows": sh,
        "clahe": 35.0,
        "denoise": iso_denoise(iso),
        "sharpen": 15.0 if iso_denoise(iso) < 30 else 5.0,
        "vibrance": 10.0,
        "lens_correction": True,
    }
    return base.merged(partial)
