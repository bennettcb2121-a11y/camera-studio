"""Google Photos import/upload against a fake Google (httpx.MockTransport): OAuth with
PKCE, Picker session polling + pagination + original downloads, appendonly uploads."""
import base64
import hashlib
import io
import json
import urllib.parse

import httpx
import numpy as np
import pytest
from PIL import Image

from backend.app.core import jobs
from backend.app.services import google_photos as G

CLIENT = {"installed": {"client_id": "cid.apps.googleusercontent.com", "client_secret": "sec",
                        "redirect_uris": ["http://localhost"]}}


def _jpeg(color=(200, 120, 40)) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(np.full((40, 60, 3), color, np.uint8)).save(buf, "JPEG")
    return buf.getvalue()


class FakeGoogle:
    def __init__(self):
        self.polls = 0
        self.uploads: list[dict] = []
        self.created: list[dict] = []
        self.albums: list[str] = []
        self.verifier_ok = None
        self.auth_headers: list[str] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        u = req.url
        if u.host == "oauth2.googleapis.com":
            form = dict(urllib.parse.parse_qsl(req.content.decode()))
            if form["grant_type"] == "authorization_code":
                self.verifier_ok = bool(form.get("code_verifier"))
                idt = "x." + base64.urlsafe_b64encode(json.dumps({"email": "charlie@example.com"}).encode()).decode().rstrip("=") + ".y"
                return httpx.Response(200, json={"access_token": "AT1", "refresh_token": "RT", "expires_in": 3600, "id_token": idt,
                                                 "scope": " ".join(G.SCOPES)})
            return httpx.Response(200, json={"access_token": "AT2", "expires_in": 3600})
        self.auth_headers.append(req.headers.get("authorization", ""))
        if u.host == "photospicker.googleapis.com":
            if req.method == "POST" and u.path == "/v1/sessions":
                return httpx.Response(200, json={"id": "S1", "pickerUri": "https://photos.google.com/picker/S1",
                                                 "pollingConfig": {"pollInterval": "1s", "timeoutIn": "60s"}})
            if req.method == "GET" and u.path == "/v1/sessions/S1":
                self.polls += 1
                return httpx.Response(200, json={"id": "S1", "mediaItemsSet": self.polls >= 2, "pollingConfig": {"pollInterval": "1s"}})
            if req.method == "DELETE":
                return httpx.Response(200, json={})
            if u.path == "/v1/mediaItems":
                if "pageToken" not in u.params:
                    return httpx.Response(200, json={"mediaItems": [
                        {"id": "a", "type": "PHOTO", "mediaFile": {"baseUrl": "https://lh3.googleusercontent.com/A", "mimeType": "image/jpeg", "filename": "DSC01.JPG"}},
                    ], "nextPageToken": "p2"})
                return httpx.Response(200, json={"mediaItems": [
                    {"id": "b", "type": "PHOTO", "mediaFile": {"baseUrl": "https://lh3.googleusercontent.com/B", "mimeType": "image/jpeg", "filename": "DSC01.JPG"}},
                ]})
        if u.host == "lh3.googleusercontent.com":
            assert str(u).endswith("=d"), "originals must be requested with =d"
            return httpx.Response(200, content=_jpeg())
        if u.host == "photoslibrary.googleapis.com":
            if u.path == "/v1/uploads":
                self.uploads.append({"mime": req.headers["x-goog-upload-content-type"], "size": len(req.content)})
                return httpx.Response(200, text=f"TOKEN{len(self.uploads)}")
            if u.path == "/v1/albums":
                self.albums.append(json.loads(req.content)["album"]["title"])
                return httpx.Response(200, json={"id": "ALB", "title": self.albums[-1]})
            if u.path == "/v1/mediaItems:batchCreate":
                body = json.loads(req.content)
                self.created.append(body)
                return httpx.Response(200, json={"newMediaItemResults": [
                    {"uploadToken": m["simpleMediaItem"]["uploadToken"], "status": {"message": "Success"},
                     "mediaItem": {"id": "m", "productUrl": "https://photos.google.com/m"}} for m in body["newMediaItems"]]})
        return httpx.Response(404, json={"error": str(u)})


@pytest.fixture()
def fake_google(monkeypatch):
    fake = FakeGoogle()
    gp = G.GooglePhotos(transport=httpx.MockTransport(fake))
    monkeypatch.setattr(G, "gphotos", gp)
    from backend.app.api import google as gapi

    monkeypatch.setattr(gapi, "gphotos", gp)
    gp.disconnect()
    G.CLIENT_PATH.unlink(missing_ok=True)
    yield fake, gp
    gp.disconnect()
    G.CLIENT_PATH.unlink(missing_ok=True)


def _connect(app_client, gp):
    r = app_client.post("/api/google/client", files={"file": ("client.json", json.dumps(CLIENT), "application/json")})
    assert r.status_code == 200
    r = app_client.get("/api/google/connect", follow_redirects=False)
    assert r.status_code in (302, 307)
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(r.headers["location"]).query))
    assert q["code_challenge_method"] == "S256" and "photospicker.mediaitems.readonly" in q["scope"]
    assert q["redirect_uri"].startswith("http://127.0.0.1:") and q["redirect_uri"].endswith("/api/google/callback")
    r = app_client.get("/api/google/callback", params={"code": "C", "state": q["state"]})
    assert "Connected to Google Photos as charlie@example.com" in r.text


def test_setup_rejects_web_client(app_client, fake_google):
    r = app_client.post("/api/google/client", files={"file": ("c.json", json.dumps({"web": CLIENT["installed"]}), "application/json")})
    assert r.status_code == 400 and "Desktop app" in r.json()["detail"]
    assert app_client.get("/api/google/status").json()["configured"] is False


def test_connect_import_and_upload(app_client, fake_google, photos):
    fake, gp = fake_google
    assert app_client.post("/api/google/import").status_code == 400  # not set up yet
    _connect(app_client, gp)
    assert fake.verifier_ok is True
    st = app_client.get("/api/google/status").json()
    assert st["configured"] and st["connected"] and st["account"] == "charlie@example.com"

    # import: picker session -> wait for selection -> 2 pages -> originals downloaded -> folder indexed
    gp_wait = gp.wait_for_selection
    gp.wait_for_selection = lambda sid, **kw: gp_wait(sid, sleep=lambda s: None, **kw)
    r = app_client.post("/api/google/import").json()
    assert r["picker_uri"] == "https://photos.google.com/picker/S1"
    j = jobs.queue.wait(r["job"]["id"])
    assert j.status == "done", j.error
    assert j.result["imported"] == 2 and fake.polls >= 2
    names = sorted(p.split("/")[-1] for p in j.result["files"])
    assert names == ["DSC01 (2).JPG", "DSC01.JPG"]  # same filename twice -> no overwrite
    items = app_client.get("/api/library/items", params={"folder": j.result["folder"]}).json()["items"]
    assert len(items) == 2
    assert all(h == "Bearer AT1" for h in fake.auth_headers)

    # upload two edited photos into the app's album
    app_client.post("/api/photos/settings", json={"path": str(photos[1]), "settings": {"exposure": 0.3}})
    r = app_client.post("/api/google/upload", json={"paths": [str(photos[1]), str(photos[2])]})
    j = jobs.queue.wait(r.json()["id"])
    assert j.status == "done", j.error
    assert j.result["uploaded"] == 2 and j.result["album"] == "Charlie's Camera Studio"
    assert [u["mime"] for u in fake.uploads] == ["image/jpeg", "image/jpeg"]
    assert fake.albums == ["Charlie's Camera Studio"] and fake.created[0]["albumId"] == "ALB"
    # second upload reuses the album
    r = app_client.post("/api/google/upload", json={"paths": [str(photos[1])]})
    assert jobs.queue.wait(r.json()["id"]).status == "done" and fake.albums == ["Charlie's Camera Studio"]
    app_client.post("/api/photos/reset", json={"path": str(photos[1])})


def test_token_refresh(fake_google, app_client):
    fake, gp = fake_google
    _connect(app_client, gp)
    tok = json.loads(G.TOKEN_PATH.read_text())
    tok["expires_at"] = 0
    G.TOKEN_PATH.write_text(json.dumps(tok))
    assert gp._token() == "AT2"
    assert json.loads(G.TOKEN_PATH.read_text())["refresh_token"] == "RT"


def test_upload_requires_connection(app_client, fake_google, photos):
    r = app_client.post("/api/google/upload", json={"paths": [str(photos[0])]})
    assert r.status_code == 409


def test_friendly_api_disabled_error():
    body = {"error": {"code": 403, "message": "Google Photos Picker API has not been used in project 236331109991 before or it "
            "is disabled. Enable it by visiting https://console.developers.google.com/apis/api/photospicker.googleapis.com/"
            "overview?project=236331109991 then retry.", "status": "PERMISSION_DENIED"}}
    msg = G._friendly_error(httpx.Response(403, json=body))
    assert msg.startswith("Photos Picker API is not turned on")
    assert msg.endswith("overview?project=236331109991")
