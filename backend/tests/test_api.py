import json

from backend.app.core.sidecar import Sidecar
from backend.app.core.settings_schema import EditSettings


def test_sidecar_undo_redo(photos):
    p = photos[0]
    Sidecar(p).path.unlink(missing_ok=True)  # fresh history
    sc = Sidecar(p)
    sc.set_settings(EditSettings(exposure=0.5), "A")
    sc.set_settings(EditSettings(exposure=1.0), "B")
    assert sc.settings.exposure == 1.0
    assert sc.undo().exposure == 0.5
    assert sc.undo().exposure == 0.0
    assert sc.redo().exposure == 0.5
    sc.set_settings(EditSettings(exposure=0.7), "C")  # branches: redo stack cleared
    h = sc.history_summary()
    assert [e["label"] for e in h["entries"]] == ["A", "C"] and h["index"] == 1
    assert sc.path.exists() and sc.path.name.endswith(".ccs.json")
    sc.reset()


def test_health_and_library(app_client, samples_dir, photos):
    r = app_client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"]
    r = app_client.post("/api/library/folders", json={"path": str(samples_dir)})
    assert r.status_code == 200 and r.json()["items"] >= len(photos)
    items = app_client.get("/api/library/items", params={"kind": "photo"}).json()["items"]
    assert len(items) == len(photos)
    vids = app_client.get("/api/library/items", params={"kind": "video"}).json()["items"]
    assert len(vids) >= 2
    r = app_client.get("/api/library/thumb", params={"path": items[0]["path"]})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    r = app_client.get("/api/library/thumb", params={"path": vids[0]["path"]})
    assert r.status_code == 200 and len(r.content) > 500
    r = app_client.post("/api/library/rating", json={"path": items[0]["path"], "rating": 4})
    assert r.json()["rating"] == 4
    rated = app_client.get("/api/library/items", params={"min_rating": 4}).json()["items"]
    assert any(i["path"] == items[0]["path"] for i in rated)
    b = app_client.get("/api/library/browse", params={"path": str(samples_dir.parent)}).json()
    assert str(samples_dir) in b["dirs"]


def test_preview_settings_history(app_client, raw_photo):
    p = str(raw_photo)
    Sidecar(raw_photo).path.unlink(missing_ok=True)  # fresh history
    info = app_client.get("/api/photos/info", params={"path": p}).json()
    assert info["is_raw"] and info["exif"]["model"] == "ILCE-6000"
    r = app_client.post("/api/photos/preview", json={"path": p, "size": 1024, "settings": {"exposure": 0.5}})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    hist = json.loads(r.headers["X-Histogram"])
    assert len(hist["lum"]) == 64
    assert float(r.headers["X-Render-Ms"]) < 400
    r = app_client.post("/api/photos/preview", json={"path": p, "size": 512, "before": True})
    assert r.status_code == 200
    r = app_client.post("/api/photos/settings", json={"path": p, "settings": {"exposure": 0.5}, "label": "Exposure"})
    assert r.json()["history"]["index"] == 0
    r = app_client.post("/api/photos/settings", json={"path": p, "settings": {"exposure": 0.5, "contrast": 20}, "label": "Contrast"})
    assert r.json()["history"]["index"] == 1
    r = app_client.post("/api/photos/undo", json={"path": p})
    assert r.json()["settings"]["contrast"] == 0
    r = app_client.post("/api/photos/redo", json={"path": p})
    assert r.json()["settings"]["contrast"] == 20
    r = app_client.post("/api/photos/auto", json={"path": p})
    assert r.json()["settings"]["lens_correction"] is True
    assert r.json()["history"]["entries"][-1]["label"] == "Auto"
    app_client.post("/api/photos/reset", json={"path": p})


def test_copy_paste_settings(app_client, photos):
    src, targets = str(photos[0]), [str(x) for x in photos[1:3]]
    app_client.post("/api/photos/settings", json={"path": src, "settings": {"vibrance": 33, "crop": {"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5}}})
    r = app_client.post("/api/photos/copy-settings", json={"source": src, "targets": targets})
    assert len(r.json()["applied"]) == 2 and "crop" not in r.json()["fields"]
    info = app_client.get("/api/photos/info", params={"path": targets[0]}).json()
    assert info["settings"]["vibrance"] == 33 and info["settings"]["crop"]["w"] == 1.0
    for t in [src] + targets:
        app_client.post("/api/photos/reset", json={"path": t})


def test_filter_strip_and_presets(app_client, photos):
    p = str(photos[1])
    r = app_client.get("/api/photos/filter-strip", params={"path": p, "size": 96})
    f = r.json()["filters"]
    assert len(f) == 26 and f[0]["id"] is None and all(len(x["thumb"]) > 100 for x in f)
    pr = app_client.get("/api/presets").json()
    assert len(pr["presets"]) >= 25 and len(pr["luts"]) >= 25
    r = app_client.post("/api/presets/apply", json={"path": p, "preset_id": "teal-orange", "strength": 0.5})
    assert r.json()["settings"]["lut"] == {"name": "teal-orange", "strength": 0.5}
    r = app_client.post("/api/presets/save", json={"name": "API saved", "path": p})
    assert r.json()["id"] == "api-saved"
    assert app_client.delete("/api/presets/api-saved").status_code == 200
    app_client.post("/api/photos/reset", json={"path": p})


def test_export_job(app_client, photos):
    r = app_client.post("/api/photos/export", json={"paths": [str(photos[1])], "format": "jpeg", "long_edge": 800})
    job = r.json()
    from backend.app.core import jobs

    j = jobs.queue.wait(job["id"])
    assert j.status == "done", j.error
    assert j.result["files"][0].endswith("_edited.jpg")
    assert app_client.get(f"/api/jobs/{job['id']}").json()["status"] == "done"


def test_mockup_api(app_client, photos):
    t = app_client.get("/api/mockups/templates").json()["templates"]
    assert len(t) >= 8
    r = app_client.post("/api/mockups/render", json={"template_id": t[0]["id"], "paths": [str(photos[0])], "long_edge": 600})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    r = app_client.post("/api/mockups/favorite", json={"path": str(photos[0]), "template_id": t[0]["id"], "favorite": True})
    assert t[0]["id"] in r.json()["mockup_favorites"]
    r = app_client.post("/api/mockups/export-all", json={"path": str(photos[1])})
    from backend.app.core import jobs

    j = jobs.queue.wait(r.json()["id"])
    assert j.status == "done" and j.result["zip"].endswith(".zip")
    import zipfile

    assert len(zipfile.ZipFile(j.result["zip"]).namelist()) >= 8
