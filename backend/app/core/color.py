"""sRGB <-> linear helpers. Everything is float32.

Transfer functions are applied as 1-D lookups through cv2.remap (multithreaded,
linearly interpolated) which is several times faster than NumPy fancy indexing at
proxy sizes. Exact 65536-entry tables are kept for the final 8/16-bit quantisation.
"""
from __future__ import annotations

import threading

import cv2
import numpy as np

_N = 65536
_M = 4096  # resolution of the remap-based 1-D LUTs (interpolated 32x by remap)
_ramp = np.linspace(0.0, 1.0, _N, dtype=np.float64)


def _srgb_encode_f(x: np.ndarray) -> np.ndarray:
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(np.maximum(x, 0), 1 / 2.4) - 0.055)


def _srgb_decode_f(x: np.ndarray) -> np.ndarray:
    return np.where(x <= 0.04045, x / 12.92, np.power((x + 0.055) / 1.055, 2.4))


ENCODE_LUT = _srgb_encode_f(_ramp).astype(np.float32)
DECODE_LUT = _srgb_decode_f(_ramp).astype(np.float32)
ENCODE_LUT_U8 = np.clip(np.round(ENCODE_LUT * 255), 0, 255).astype(np.uint8)
ENCODE_LUT_U16 = np.clip(np.round(ENCODE_LUT * 65535), 0, 65535).astype(np.uint16)

# Rec.709 luminance weights for linear sRGB primaries.
LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
_LUMA_M = LUMA.reshape(1, 3)

_zero_maps: dict[tuple, np.ndarray] = {}
_zero_lock = threading.Lock()


def _zeros(shape: tuple) -> np.ndarray:
    with _zero_lock:
        z = _zero_maps.get(shape)
        if z is None:
            z = np.zeros(shape, np.float32)
            if len(_zero_maps) > 8:
                _zero_maps.clear()
            _zero_maps[shape] = z
        return z


def lut1d_from_table(table65536: np.ndarray) -> np.ndarray:
    """Downsample an exact 65536 table to the 1 x 4096 image remap wants."""
    idx = np.linspace(0, _N - 1, _M).round().astype(np.int64)
    return np.ascontiguousarray(table65536[idx].reshape(1, _M).astype(np.float32))


def apply_lut1d(img: np.ndarray, lut_row: np.ndarray) -> np.ndarray:
    """Apply a 1 x M float LUT (domain [0,1]) to every channel of img, interpolated."""
    img = np.ascontiguousarray(img, dtype=np.float32)
    shape = img.shape
    flat = img.reshape(shape[0], -1)
    mx = cv2.multiply(flat, float(lut_row.shape[1] - 1))
    out = cv2.remap(lut_row, mx, _zeros(flat.shape), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return out.reshape(shape)


ENCODE_ROW = lut1d_from_table(ENCODE_LUT)
DECODE_ROW = lut1d_from_table(DECODE_LUT)


def _index(x: np.ndarray) -> np.ndarray:
    """Saturating, rounded 16-bit index (clips negatives and >1)."""
    x = np.ascontiguousarray(x, dtype=np.float32)
    if x.ndim == 3 and x.shape[2] == 3:
        return cv2.multiply(x, (_N - 1, _N - 1, _N - 1, 0), dtype=cv2.CV_16U)
    return cv2.multiply(x, float(_N - 1), dtype=cv2.CV_16U)


def encode(lin: np.ndarray) -> np.ndarray:
    """Linear float32 -> sRGB-encoded float32 (values outside [0,1] are clipped)."""
    return apply_lut1d(lin, ENCODE_ROW)


def decode(enc: np.ndarray) -> np.ndarray:
    return apply_lut1d(enc, DECODE_ROW)


def to_uint8(lin: np.ndarray) -> np.ndarray:
    return ENCODE_LUT_U8[_index(lin)]


def to_uint16(lin: np.ndarray) -> np.ndarray:
    return ENCODE_LUT_U16[_index(lin)]


def uint8_to_linear(img8: np.ndarray) -> np.ndarray:
    lut = DECODE_LUT[np.linspace(0, _N - 1, 256).round().astype(np.int64)]
    return lut[img8]


def uint16_to_linear(img16: np.ndarray) -> np.ndarray:
    return DECODE_LUT[img16]


def luminance(rgb: np.ndarray) -> np.ndarray:
    return cv2.transform(np.ascontiguousarray(rgb, dtype=np.float32), _LUMA_M)


def smoothstep(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def mul3(img: np.ndarray, gains) -> np.ndarray:
    """Multiply a 3-channel image by a per-channel triple (fast, multithreaded)."""
    g = [float(v) for v in gains]
    return cv2.multiply(np.ascontiguousarray(img, dtype=np.float32), (g[0], g[1], g[2], 0.0))


def mul_mask(img: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Multiply a 3-channel image by a single-channel gain map."""
    return cv2.multiply(np.ascontiguousarray(img, dtype=np.float32), cv2.merge([mask, mask, mask]))


def exp2(x: np.ndarray) -> np.ndarray:
    return cv2.exp(cv2.multiply(np.ascontiguousarray(x, dtype=np.float32), float(np.log(2.0))))
