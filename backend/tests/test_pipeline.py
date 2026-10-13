import os
import time

import numpy as np
import pytest
from PIL import Image

from backend.app.api.photos import export_one
from backend.app.core import color
from backend.app.core.images import proxy_cache, resize_long_edge, read_exif
from backend.app.core.luts import Lut3D, load_lut, list_luts
from backend.app.core.pipeline import crop_rect_for_aspect, finalize, histogram, render
from backend.app.core.settings_schema import EditSettings


def test_srgb_roundtrip():
    x = np.random.rand(64, 64, 3).astype(np.float32)
    back = color.decode(color.encode(x))
    assert np.abs(back - x).max() < 2e-3


def test_identity_is_passthrough():
    x = np.random.rand(32, 48, 3).astype(np.float32)
    out = render(x, EditSettings())
    assert np.array_equal(out, x)


def test_exposure_doubles_linear():
    x = np.full((8, 8, 3), 0.2, np.float32)
    out = render(x, EditSettings(exposure=1.0))
    assert np.allclose(out, 0.4, atol=1e-4)


def test_wb_preserves_grey_luminance():
    x = np.full((8, 8, 3), 0.3, np.float32)
    out = render(x, EditSettings().merged({"wb": {"temp": 40, "tint": -20}}))
    assert out[0, 0, 0] > out[0, 0, 2]  # warmer: more red than blue
    assert abs(float(color.luminance(out).mean()) - 0.3) < 0.01


def test_tone_curve_monotone_and_contrast():
    ramp = np.linspace(0, 1, 256, dtype=np.float32)[None, :, None].repeat(3, axis=2)
    out = render(ramp, EditSettings(contrast=50))
    lum = out[0, :, 0]
    assert np.all(np.diff(lum) >= -1e-4)
    enc = color.encode(out)[0, :, 0]
    assert enc[200] - enc[56] > color.encode(ramp)[0, 200, 0] - color.encode(ramp)[0, 56, 0]


def test_highlights_only_touch_bright_pixels():
    x = np.zeros((2, 2, 3), np.float32)
    x[0] = 0.02
    x[1] = 0.8
    out = render(x, EditSettings(highlights=-60))
    assert np.allclose(out[0], x[0], atol=1e-4)
    assert out[1, 0, 0] < 0.8


def test_lut_remap_matches_reference():
    lut = Lut3D.from_function(lambda c: np.clip(c**1.3 * np.array([1.1, 1.0, 0.9]), 0, 1), 17)
    enc = np.random.rand(40, 60, 3).astype(np.float32)
    fast, ref = lut.apply(enc), lut.apply_exact(enc)
    assert np.abs(fast - ref).max() < 2e-3


def test_cube_write_read_roundtrip(tmp_path):
    lut = Lut3D.from_function(lambda c: 1 - c, 9, "inv")
    lut.write_cube(tmp_path / "inv.cube")
    back = Lut3D.read_cube(tmp_path / "inv.cube")
    assert back.size == 9 and np.abs(back.table - lut.table).max() < 1e-5


def test_builtin_luts_load():
    names = list_luts()
    assert len(names) >= 25
    for n in names:
        assert load_lut(n) is not None


def test_vignette_follows_lightroom_sign():
    x = np.full((60, 90, 3), 0.3, np.float32)
    dark = render(x, EditSettings(vignette=-50))
    light = render(x, EditSettings(vignette=50))
    assert dark[0, 0, 0] < 0.3 < light[0, 0, 0]
    assert abs(dark[30, 45, 0] - 0.3) < 0.01  # centre untouched


def test_crop_aspect_presets():
    r = crop_rect_for_aspect(6000, 4000, "1:1")
    assert abs(r["w"] * 6000 - r["h"] * 4000) < 1
    r = crop_rect_for_aspect(6000, 4000, "9:16")
    assert abs((r["w"] * 6000) / (r["h"] * 4000) - 9 / 16) < 1e-3
    x = np.random.rand(40, 60, 3).astype(np.float32)
    out = render(x, EditSettings().merged({"crop": {"x": 0.25, "y": 0.0, "w": 0.5, "h": 1.0}}))
    assert out.shape == (40, 30, 3)


def test_histogram_stats_shape():
    x = np.random.rand(32, 32, 3).astype(np.float32)
    h = histogram(x)
    assert len(h["lum"]) == 64 and 0 <= h["stats"]["median"] <= 1


@pytest.mark.parametrize("field,value", [("exposure", 0.7), ("highlights", -50), ("contrast", 30), ("vibrance", 30),
                                         ("clarity", 40), ("denoise", 50), ("sharpen", 50), ("vignette", 40), ("clahe", 40)])
def test_single_slider_preview_under_150ms(photos, field, value):
    """Acceptance 2: slider changes on the 2048px proxy render fast."""
    p = photos[0]
    proxy, fw, fh = proxy_cache.get(p)
    s = EditSettings().merged({field: value})
    render(proxy, s, proxy.shape[1] / fw)  # warm caches
    best = 1e9
    for _ in range(3):
        t = time.perf_counter()
        finalize(render(proxy, s, proxy.shape[1] / fw))
        best = min(best, time.perf_counter() - t)
    # 150 ms is the product target on Charlie's laptop; shared CI runners are several
    # times slower, so there we only guard against pathological regressions.
    limit = 0.150 if not os.environ.get("CI") else 0.900
    assert best < limit, f"{field} took {best * 1000:.0f} ms"


def test_export_matches_preview(raw_photo, tmp_path, test_output):
    """Acceptance 2: exported JPEG vs 2048px preview, mean abs pixel diff < 2%."""
    s = EditSettings().merged({"exposure": 0.4, "contrast": 15, "highlights": -30, "shadows": 20, "vibrance": 20,
                               "clarity": 20, "sharpen": 25, "vignette": 20, "wb": {"temp": 8},
                               "lut": {"name": "golden-hour", "strength": 0.8}})
    proxy, fw, fh = proxy_cache.get(raw_photo)
    preview = finalize(render(proxy, s, proxy.shape[1] / fw))
    out = export_one(raw_photo, "jpeg", 95, None, tmp_path, s)
    exp = np.array(Image.open(out).convert("RGB"))
    assert exp.shape[1] >= 5900  # full resolution
    exp_small = resize_long_edge(exp, max(preview.shape[:2]))
    if exp_small.shape != preview.shape:
        exp_small = np.array(Image.fromarray(exp_small).resize((preview.shape[1], preview.shape[0])))
    diff = np.abs(exp_small.astype(np.float32) - preview.astype(np.float32)).mean() / 255
    Image.fromarray(preview).save(test_output / "accept2_preview.jpg", quality=92)
    Image.fromarray(exp_small).save(test_output / "accept2_export_downscaled.jpg", quality=92)
    assert diff < 0.02, f"mean diff {diff:.4f}"


def test_export_png_and_tiff16(photos, tmp_path):
    p = next(x for x in photos if x.suffix.lower() == ".jpg")
    png = export_one(p, "png", 90, 800, tmp_path)
    tif = export_one(p, "tiff16", 90, 800, tmp_path)
    assert Image.open(png).mode == "RGB"
    import tifffile

    arr = tifffile.imread(tif)
    assert arr.dtype == np.uint16 and arr.shape[2] == 3


def test_exif_read(photos):
    for p in photos:
        e = read_exif(p)
        assert e["iso"] is not None and e["aperture"] is not None, p.name
