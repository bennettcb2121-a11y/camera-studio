"""Loading originals (ARW/JPG/PNG/TIFF) into linear float32, EXIF, and the proxy cache.

Originals are never written to. Proxies (2048px linear float16 .npy) and thumbnails
live in the .cache folder and are keyed by path + mtime.
"""
from __future__ import annotations

import hashlib
import logging
import threading
from collections import OrderedDict
from pathlib import Path

import cv2
import exifread
import numpy as np
import rawpy
from PIL import Image, ImageOps

from ..config import PROXY_DIR, PROXY_LONG_EDGE, RAW_EXTS, THUMB_DIR, THUMB_LONG_EDGE
from . import color

log = logging.getLogger(__name__)

# libraw's no_auto_bright output sits well below a camera JPEG; this matches typical
# "baseline exposure" so slider 0 looks like the camera's own rendering.
RAW_BASELINE_EV = 0.7
CACHE_VERSION = "v2"  # bump when decoding/baseline changes so stale proxies are rebuilt


def is_raw(path: Path) -> bool:
    return path.suffix.lower() in RAW_EXTS


def cache_key(path: Path) -> str:
    st = path.stat()
    return hashlib.sha1(f"{CACHE_VERSION}|{path.resolve()}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()[:20]


def _apply_lensfun(lin: np.ndarray, exif: dict) -> np.ndarray:
    """Vignetting + distortion correction via lensfun when the lens is known."""
    try:
        import lensfunpy  # noqa
    except Exception:
        return lin
    try:
        db = lensfunpy.Database()
        cams = db.find_cameras(exif.get("make") or "Sony", exif.get("model") or "ILCE-6000")
        if not cams:
            return lin
        lens_name = exif.get("lens") or ""
        lenses = db.find_lenses(cams[0], None, lens_name) if lens_name else []
        if not lenses:
            return lin
        lens = lenses[0]
        focal = float(exif.get("focal_length") or lens.min_focal)
        aperture = float(exif.get("aperture") or 5.6)
        h, w = lin.shape[:2]
        mod = lensfunpy.Modifier(lens, cams[0].crop_factor, w, h)
        mod.initialize(focal, aperture, 1000.0, pixel_format=np.float32)
        img = np.ascontiguousarray(lin, dtype=np.float32)
        did = mod.apply_color_modification(img)
        coords = mod.apply_geometry_distortion()
        if coords is not None:
            img = cv2.remap(img, coords, None, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        return img if (did or coords is not None) else lin
    except Exception as e:  # lensfun errors must never break an edit
        log.info("lensfun skipped: %s", e)
        return lin


def load_linear(path: Path, half_size: bool = False, lens_correction: bool = False, exif: dict | None = None) -> np.ndarray:
    """Decode an original to linear float32 RGB (sRGB primaries), orientation applied."""
    if is_raw(path):
        with rawpy.imread(str(path)) as raw:
            rgb16 = raw.postprocess(
                use_camera_wb=True,
                output_color=rawpy.ColorSpace.sRGB,
                gamma=(1, 1),
                no_auto_bright=True,
                output_bps=16,
                half_size=half_size,
                demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD if not half_size else None,
                user_flip=None,
            )
        lin = rgb16.astype(np.float32) / 65535.0
        if RAW_BASELINE_EV:
            lin *= 2.0**RAW_BASELINE_EV
    else:
        im = Image.open(path)
        im = ImageOps.exif_transpose(im)
        if im.mode in ("I;16", "I;16B", "I;16L"):
            arr = np.array(im).astype(np.uint16)
            arr = np.repeat(arr[..., None], 3, axis=2)
            lin = color.uint16_to_linear(arr)
        else:
            arr = np.array(im.convert("RGB"))
            lin = color.uint8_to_linear(arr)
    if lens_correction:
        lin = _apply_lensfun(lin, exif or read_exif(path))
    return lin


def resize_long_edge(img: np.ndarray, long_edge: int) -> np.ndarray:
    h, w = img.shape[:2]
    if max(h, w) <= long_edge:
        return img
    s = long_edge / max(h, w)
    return cv2.resize(img, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)


# ------------------------------------------------------------------ EXIF
def _ratio(v) -> float | None:
    try:
        if hasattr(v, "num"):
            return float(v.num) / float(v.den) if v.den else None
        return float(v)
    except Exception:
        return None


def read_exif(path: Path) -> dict:
    out: dict = {"make": None, "model": None, "iso": None, "shutter": None, "aperture": None, "lens": None,
                 "focal_length": None, "datetime": None, "width": None, "height": None}
    try:
        with open(path, "rb") as f:
            tags = exifread.process_file(f, details=False)
    except Exception:
        return out

    def g(*keys):
        for k in keys:
            if k in tags:
                return tags[k]
        return None

    v = g("Image Make"); out["make"] = str(v).strip() if v else None
    v = g("Image Model"); out["model"] = str(v).strip() if v else None
    v = g("EXIF ISOSpeedRatings"); out["iso"] = int(str(v)) if v and str(v).isdigit() else None
    v = g("EXIF ExposureTime")
    if v is not None and v.values:
        r = v.values[0]
        out["shutter"] = f"{r.num}/{r.den}" if hasattr(r, "num") and r.num < r.den else (str(_ratio(r)) if _ratio(r) else None)
    v = g("EXIF FNumber")
    if v is not None and v.values:
        out["aperture"] = _ratio(v.values[0])
    v = g("EXIF LensModel", "MakerNote LensModel", "EXIF LensSpecification")
    out["lens"] = str(v).strip() if v else None
    v = g("EXIF FocalLength")
    if v is not None and v.values:
        out["focal_length"] = _ratio(v.values[0])
    v = g("EXIF DateTimeOriginal", "Image DateTime"); out["datetime"] = str(v) if v else None
    v = g("EXIF ExifImageWidth", "Image ImageWidth"); out["width"] = int(str(v)) if v and str(v).isdigit() else None
    v = g("EXIF ExifImageLength", "Image ImageLength"); out["height"] = int(str(v)) if v and str(v).isdigit() else None
    return out


# ------------------------------------------------------------------ proxy cache
class ProxyCache:
    """Memory LRU + on-disk float16 .npy of the 2048px linear proxy."""

    def __init__(self, max_items: int = 12):
        self._mem: OrderedDict[str, tuple[np.ndarray, int, int]] = OrderedDict()
        self._max = max_items
        self._lock = threading.Lock()

    def _disk(self, key: str) -> Path:
        return PROXY_DIR / f"{key}.npy"

    def get(self, path: Path, lens_correction: bool = False) -> tuple[np.ndarray, int, int]:
        """Returns (linear float32 proxy, full_width, full_height)."""
        key = cache_key(path) + ("_lc" if lens_correction else "")
        with self._lock:
            if key in self._mem:
                self._mem.move_to_end(key)
                return self._mem[key]
        disk = self._disk(key)
        if disk.exists():
            try:
                blob = np.load(disk, allow_pickle=False)
                full_w, full_h = int(blob[0, 0, 0]), int(blob[0, 0, 1])
                proxy = blob[1:].astype(np.float32)
                item = (proxy, full_w, full_h)
                self._put(key, item)
                return item
            except Exception:
                disk.unlink(missing_ok=True)
        exif = read_exif(path)
        lin = load_linear(path, half_size=is_raw(path), lens_correction=lens_correction, exif=exif)
        full_h, full_w = lin.shape[:2]
        if is_raw(path):
            full_h, full_w = full_h * 2, full_w * 2
        proxy = np.ascontiguousarray(resize_long_edge(lin, PROXY_LONG_EDGE))
        header = np.zeros((1,) + proxy.shape[1:], dtype=np.float16)
        header[0, 0, 0], header[0, 0, 1] = full_w, full_h
        try:
            np.save(disk, np.concatenate([header, proxy.astype(np.float16)], axis=0))
        except Exception as e:
            log.warning("proxy disk cache write failed: %s", e)
        item = (proxy, full_w, full_h)
        self._put(key, item)
        return item

    def _put(self, key, item):
        with self._lock:
            self._mem[key] = item
            self._mem.move_to_end(key)
            while len(self._mem) > self._max:
                self._mem.popitem(last=False)

    def invalidate(self, path: Path):
        key = cache_key(path)
        with self._lock:
            for k in [k for k in self._mem if k.startswith(key)]:
                del self._mem[k]
        for p in PROXY_DIR.glob(f"{key}*.npy"):
            p.unlink(missing_ok=True)


proxy_cache = ProxyCache()


def thumbnail_path(path: Path) -> Path:
    return THUMB_DIR / f"{cache_key(path)}.jpg"


def ensure_thumbnail(path: Path) -> Path:
    tp = thumbnail_path(path)
    if tp.exists():
        return tp
    if is_raw(path):
        try:
            with rawpy.imread(str(path)) as raw:
                thumb = raw.extract_thumb()
            if thumb.format == rawpy.ThumbFormat.JPEG:
                im = Image.open(__import__("io").BytesIO(thumb.data))
                im = ImageOps.exif_transpose(im)
                im.thumbnail((THUMB_LONG_EDGE, THUMB_LONG_EDGE))
                im.convert("RGB").save(tp, quality=85)
                return tp
        except Exception:
            pass
    proxy, _, _ = proxy_cache.get(path)
    small = resize_long_edge(proxy, THUMB_LONG_EDGE)
    Image.fromarray(color.to_uint8(small)).save(tp, quality=85)
    return tp
