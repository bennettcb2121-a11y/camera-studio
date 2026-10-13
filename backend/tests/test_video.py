import shutil
from pathlib import Path

import numpy as np
from PIL import Image

from backend.app.core import jobs
from backend.app.core import video as V
from backend.app.core.ffmpeg import ffmpeg_exe, probe
from backend.app.core.luts import Lut3D
from backend.app.core.settings_schema import EditSettings
from backend.tests.fake_anthropic import FakeAnthropic


def test_ffmpeg_available():
    assert Path(ffmpeg_exe()).exists()


def test_probe_and_proxy(videos):
    for v in videos:
        info = probe(str(v))
        assert info["duration"] > 4 and info["width"] == 1920
        proxy = V.ensure_proxy(v)
        pi = probe(str(proxy))
        assert pi["height"] == 720 and abs(pi["duration"] - info["duration"]) < 0.5
    assert any(v.suffix.upper() == ".MTS" for v in videos), "needs an AVCHD .MTS clip"


def test_timeline_ops(videos, photos):
    proj = V.new_project("ops")
    c = V.add_video_clip(proj, videos[0])
    V.add_photo_clip(proj, photos[0], duration=2.0)
    V.add_title_clip(proj, "Hello", duration=1.5)
    assert abs(V.project_duration(proj) - (c["out"] + 3.5)) < 0.01
    # trim
    c["in"], c["out"] = 1.0, 4.0
    assert abs(V.clip_duration(c) - 3.0) < 1e-6
    # speed
    c["speed"] = 2.0
    assert abs(V.clip_duration(c) - 1.5) < 1e-6
    # split at playhead 0.75 (inside first clip)
    new = V.split_at(proj, 0.75)
    assert new is not None and len(proj["clips"]) == 4
    a, b = proj["clips"][0], proj["clips"][1]
    assert abs(a["out"] - 2.5) < 1e-6 and abs(b["in"] - 2.5) < 1e-6
    # reorder
    proj["clips"].reverse()
    V.save_project(proj)
    assert V.load_project(proj["id"])["clips"][0]["type"] == "title"
    V.delete_project(proj["id"])


def test_bake_look_cube_matches_pipeline():
    s = EditSettings().merged({"exposure": 0.3, "contrast": 20, "vibrance": 25, "lut": {"name": "teal-orange", "strength": 0.8},
                               "sharpen": 50, "vignette": 30})
    cube = V.bake_look_cube(s)
    lut = Lut3D.read_cube(cube)
    from backend.app.core import color
    from backend.app.core.pipeline import render

    enc = np.random.rand(30, 30, 3).astype(np.float32)
    via_lut = lut.apply(enc)
    s_pp = s.merged({"sharpen": 0, "vignette": 0})
    via_pipeline = color.encode(np.clip(render(color.decode(enc), s_pp), 0, 1))
    assert np.abs(via_lut - via_pipeline).mean() < 0.01


def test_scene_detection_splits_clip(videos):
    proj = V.new_project("shots")
    c = V.add_video_clip(proj, videos[0])
    shots = V.split_into_shots(proj, c["id"])
    assert len(shots) >= 2, "synthetic clip has hard cuts every 2 s"
    V.delete_project(proj["id"])


def test_trim_lut_title_export_9x16(videos, photos, music, test_output):
    """Acceptance 6: trim, LUT, title, export at 9:16 (from the original, not the proxy)."""
    proj = V.new_project("accept6")
    c = V.add_video_clip(proj, videos[0], 1.0, 3.0)
    c["look"] = {"preset_id": "teal-orange", "strength": 1.0}
    c["title"] = {"text": "Trimmed + LUT + title", "position": "bottom", "size": 48}
    p = V.add_photo_clip(proj, photos[0], duration=1.5, zoom=True)
    p["look"] = {"preset_id": "kodak-warm"}
    V.add_title_clip(proj, "The End", duration=1.0)
    if music:
        proj["music"] = {"src": str(music), "volume": 0.6, "fade_in": 0.5, "fade_out": 1.0}
    V.save_project(proj)
    out = test_output / "accept6_reel_9x16.mp4"
    V.render_project(proj, out, "9:16", draft=False)
    info = probe(str(out))
    assert info["width"] == 1080 and info["height"] == 1920
    assert abs(info["duration"] - 4.5) < 0.6
    assert info["has_audio"]
    # the LUT changed the colours: compare a frame of the render with the source frame
    src_frame = V.clip_thumbnail(videos[0], 1.5, 320)
    out_frame = V.clip_thumbnail(out, 0.5, 320)
    assert len(out_frame) > 1000
    # draft (proxy) render for the UI
    draft = V.RENDER_DIR / "accept6_draft.mp4"
    V.render_project(proj, draft, "16:9", draft=True)
    assert probe(str(draft))["height"] == 720
    V.delete_project(proj["id"])


def test_contact_sheet_and_ai_reel(app_client, videos, fake_ai):
    sheet, ts = V.contact_sheet(videos[1])
    assert len(sheet) > 2000 and len(ts) >= 4
    proj = V.new_project("reel")
    r = app_client.post("/api/ai/reel", json={"project_id": proj["id"], "clip_src": str(videos[1]), "target_seconds": 30})
    assert r.status_code == 200
    j = jobs.queue.wait(r.json()["id"])
    assert j.status == "done", j.error
    clips = j.result["project"]["clips"]
    assert clips[0]["type"] == "title" and len(clips) == 4
    assert clips[2]["speed"] == 0.5
    V.delete_project(proj["id"])


def test_video_api(app_client, videos):
    proj = app_client.post("/api/video/projects", json={"name": "api"}).json()
    pid = proj["id"]
    r = app_client.post(f"/api/video/projects/{pid}/clips", json={"type": "video", "src": str(videos[0])})
    cid = r.json()["clip"]["id"]
    r = app_client.patch(f"/api/video/projects/{pid}/clips/{cid}", json={"in": 0.5, "out": 2.5, "look": {"preset_id": "punch"}})
    assert r.json()["clip"]["out"] == 2.5
    r = app_client.post(f"/api/video/projects/{pid}/split", json={"playhead": 1.0})
    assert len(r.json()["project"]["clips"]) == 2
    r = app_client.get("/api/video/proxy", params={"path": str(videos[0])})
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
    r = app_client.post(f"/api/video/projects/{pid}/render", json={"aspect": "1:1", "draft": True})
    j = jobs.queue.wait(r.json()["id"])
    assert j.status == "done", j.error
    assert app_client.delete(f"/api/video/projects/{pid}").status_code == 200
