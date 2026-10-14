import numpy as np
from PIL import Image

from backend.app.core.auto import auto_settings
from backend.app.core.images import proxy_cache, read_exif, resize_long_edge
from backend.app.core.mockups import get_template, list_templates, render_mockup, save_template
from backend.app.core.pipeline import finalize, render
from backend.app.core.presets import apply_preset, list_presets, save_user_preset, delete_user_preset
from backend.app.core.settings_schema import EditSettings


def _edited(p, s=None, long_edge=600):
    proxy, fw, fh = proxy_cache.get(p)
    small = resize_long_edge(proxy, long_edge)
    return finalize(render(small, s or EditSettings(), small.shape[1] / fw))


def test_25_filters_in_5_groups():
    presets = [p for p in list_presets() if not p["user"]]
    assert len(presets) == 25
    groups = {p["group"] for p in presets}
    assert groups == {"Film", "Travel", "Portrait", "Sport", "Utility"}
    assert all(p["settings"]["lut"]["name"] for p in presets)


def test_all_filters_and_auto_on_every_image(photos, test_output):
    """Acceptance 3: all 25 filters and Auto apply without errors to every test image."""
    presets = [p for p in list_presets() if not p["user"]]
    for p in photos:
        proxy, fw, fh = proxy_cache.get(p)
        small = resize_long_edge(proxy, 400)
        base = EditSettings()
        outs = []
        for rec in presets:
            s = apply_preset(base, rec["id"])
            out = finalize(render(small, s, small.shape[1] / fw))
            assert out.shape == small.shape and out.dtype == np.uint8
            assert out.std() > 1, f"{rec['id']} produced a flat image on {p.name}"
            outs.append(out)
        # filters must differ from each other (they are "original", not duplicates)
        means = np.array([o.mean(axis=(0, 1)) for o in outs])
        assert len(np.unique(means.round(1), axis=0)) >= 20
        auto = auto_settings(proxy, read_exif(p), base)
        out = finalize(render(small, auto, small.shape[1] / fw))
        assert out.shape == small.shape
        Image.fromarray(out).save(test_output / f"accept3_auto_{p.stem}.jpg", quality=85)


def test_filter_strength_blends():
    x = np.random.rand(16, 16, 3).astype(np.float32) * 0.8
    full = render(x, EditSettings().merged({"lut": {"name": "sepia", "strength": 1.0}}))
    half = render(x, EditSettings().merged({"lut": {"name": "sepia", "strength": 0.5}}))
    none = render(x, EditSettings().merged({"lut": {"name": "sepia", "strength": 0.0}}))
    assert np.allclose(none, x, atol=2e-3)
    assert np.abs(half - x).mean() < np.abs(full - x).mean()


def test_user_preset_roundtrip():
    rec = save_user_preset("My test look", EditSettings(exposure=0.3, vibrance=20), notes="test")
    assert rec["user"] and rec["settings"]["exposure"] == 0.3
    s = apply_preset(EditSettings(), rec["id"])
    assert s.vibrance == 20
    assert delete_user_preset(rec["id"])


def test_all_mockups_render_for_every_image(photos, test_output):
    """Acceptance 4: all 8 templates render for every test image."""
    templates = list_templates()
    assert len(templates) >= 8
    photos_rgb = [_edited(p) for p in photos]
    for p, img in zip(photos, photos_rgb):
        for t in templates:
            out = render_mockup(t, [img] + photos_rgb)
            bg = np.array(Image.open(f"mockups/{t['id']}/background.jpg"))
            assert out.shape == bg.shape
            # the slot area must actually contain the photo (differs from the background)
            c = np.array(t["slots"][0]["corners"]).mean(axis=0).astype(int)
            assert np.abs(out[c[1], c[0]].astype(int) - bg[c[1], c[0]].astype(int)).sum() > 0 or img.std() < 1
        Image.fromarray(render_mockup(templates[0], [img])).save(test_output / f"accept4_mockup_{p.stem}.jpg", quality=85)


def test_mockup_perspective_alignment():
    """A photo with a known colour must land exactly inside the template's corners."""
    t = get_template("04-laptop")
    img = np.full((300, 500, 3), (255, 0, 0), np.uint8)
    out = render_mockup(t, [img])
    quad = np.array(t["slots"][0]["corners"], dtype=np.float32)
    centre = quad.mean(axis=0).astype(int)
    assert out[centre[1], centre[0], 0] > 200 and out[centre[1], centre[0], 1] < 60
    # just outside the top-left corner stays background-coloured (not red)
    outside = (quad[0] - 12).astype(int)
    assert not (out[outside[1], outside[0], 0] > 200 and out[outside[1], outside[0], 1] < 60)


def test_template_editor_save(tmp_path, photos):
    bg = tmp_path / "bg.jpg"
    Image.fromarray(np.full((400, 600, 3), 120, np.uint8)).save(bg)
    rec = save_template("zz-test-template", "Test template", [{"corners": [[100, 50], [500, 60], [490, 350], [110, 340]]}],
                        background=bg)
    assert rec["id"] == "zz-test-template" and len(rec["slots"]) == 1
    out = render_mockup(rec, [_edited(photos[0])])
    assert out.shape == (400, 600, 3)
    import shutil

    shutil.rmtree(f"mockups/{rec['id']}")
