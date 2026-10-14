"""Google Photos: import with the Photos Picker API, upload edits with the Library API.

Since 31 March 2025 Google only lets apps (a) open the Google-hosted Picker so the user
chooses items to hand over, and (b) upload media / create albums with
`photoslibrary.appendonly`. Browsing the whole library is no longer possible for any app.

Setup (once, by Charlie): a Google Cloud OAuth client of type "Desktop app" with the
Photos Picker API and Photos Library API enabled. Its JSON goes in .cache/ (never in git).
Sign-in is OAuth 2.0 with PKCE and a loopback redirect to this app's own server:
    http://127.0.0.1:<port>/api/google/callback
Tokens are stored in .cache/google_token.json and refreshed automatically.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
import urllib.parse
from pathlib import Path
from typing import Callable

import httpx

from ..config import DATA_DIR

CLIENT_PATH = DATA_DIR / "google_client.json"
TOKEN_PATH = DATA_DIR / "google_token.json"
STATE_PATH = DATA_DIR / "google_state.json"  # album id etc.

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
PICKER = "https://photospicker.googleapis.com/v1"
LIBRARY = "https://photoslibrary.googleapis.com/v1"
SCOPES = [
    "https://www.googleapis.com/auth/photospicker.mediaitems.readonly",
    "https://www.googleapis.com/auth/photoslibrary.appendonly",
]
ALBUM_TITLE = "Charlie's Camera Studio"
UPLOAD_EXTS = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".tif": "image/tiff", ".tiff": "image/tiff",
               ".mp4": "video/mp4", ".mov": "video/quicktime"}


class GoogleError(RuntimeError):
    pass


def _read(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def _write(p: Path, data: dict) -> None:
    p.write_text(json.dumps(data, indent=1))
    try:
        p.chmod(0o600)
    except OSError:
        pass


class GooglePhotos:
    def __init__(self, transport: httpx.BaseTransport | None = None):
        # injectable transport so tests can fake Google without network access
        self._http = httpx.Client(transport=transport, timeout=60, follow_redirects=True)
        self._pending: dict[str, dict] = {}  # oauth state -> {verifier, redirect_uri}

    # ------------------------------------------------------------------ setup
    def client(self) -> dict | None:
        d = _read(CLIENT_PATH)
        if not d:
            return None
        c = d.get("installed") or d.get("web") or d
        return c if c.get("client_id") else None

    def save_client(self, raw: dict | str) -> dict:
        d = json.loads(raw) if isinstance(raw, str) else raw
        c = d.get("installed") or d.get("web") or d
        if not c.get("client_id") or not c.get("client_secret"):
            raise GoogleError("That file doesn't look like a Google OAuth client JSON (no client_id / client_secret).")
        if "web" in d:
            raise GoogleError("Please create the OAuth client as type 'Desktop app', not 'Web application'.")
        _write(CLIENT_PATH, d)
        return {"client_id": c["client_id"]}

    def status(self) -> dict:
        tok = _read(TOKEN_PATH) or {}
        return {"configured": self.client() is not None, "connected": bool(tok.get("refresh_token")),
                "account": tok.get("email"), "album": (_read(STATE_PATH) or {}).get("album_title")}

    def disconnect(self) -> None:
        TOKEN_PATH.unlink(missing_ok=True)
        STATE_PATH.unlink(missing_ok=True)

    # ------------------------------------------------------------------ OAuth (PKCE + loopback)
    def auth_url(self, redirect_uri: str) -> str:
        c = self.client()
        if not c:
            raise GoogleError("Google Photos isn't set up yet: add your OAuth client JSON first.")
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(16)
        self._pending[state] = {"verifier": verifier, "redirect_uri": redirect_uri, "t": time.time()}
        q = {"client_id": c["client_id"], "redirect_uri": redirect_uri, "response_type": "code", "scope": " ".join(SCOPES + ["email"]),
             "access_type": "offline", "prompt": "consent", "code_challenge": challenge, "code_challenge_method": "S256",
             "state": state, "include_granted_scopes": "true"}
        return AUTH_URL + "?" + urllib.parse.urlencode(q)

    def finish_auth(self, code: str, state: str) -> dict:
        pend = self._pending.pop(state, None)
        if not pend:
            raise GoogleError("Sign-in expired or came from somewhere else. Please click Connect again.")
        c = self.client()
        r = self._http.post(TOKEN_URL, data={"code": code, "client_id": c["client_id"], "client_secret": c["client_secret"],
                                             "redirect_uri": pend["redirect_uri"], "grant_type": "authorization_code",
                                             "code_verifier": pend["verifier"]})
        if r.status_code != 200:
            raise GoogleError(f"Google refused the sign-in: {r.text[:300]}")
        tok = r.json()
        tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
        tok["email"] = _email_from_id_token(tok.get("id_token"))
        granted = set((tok.get("scope") or "").split())
        missing = [s for s in SCOPES if s not in granted] if granted else []
        _write(TOKEN_PATH, tok)
        return {"email": tok["email"], "missing_scopes": missing}

    def _token(self) -> str:
        tok = _read(TOKEN_PATH)
        if not tok or not tok.get("refresh_token"):
            raise GoogleError("Not connected to Google Photos.")
        if time.time() < tok.get("expires_at", 0):
            return tok["access_token"]
        c = self.client()
        r = self._http.post(TOKEN_URL, data={"client_id": c["client_id"], "client_secret": c["client_secret"],
                                             "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"})
        if r.status_code != 200:
            raise GoogleError("Google sign-in expired. Please connect again." if r.status_code in (400, 401) else r.text[:300])
        new = r.json()
        tok.update(access_token=new["access_token"], expires_at=time.time() + int(new.get("expires_in", 3600)) - 60)
        _write(TOKEN_PATH, tok)
        return tok["access_token"]

    def _req(self, method: str, url: str, **kw) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._token()}", **kw.pop("headers", {})}
        r = self._http.request(method, url, headers=headers, **kw)
        if r.status_code >= 400:
            raise GoogleError(_friendly_error(r))
        return r

    # ------------------------------------------------------------------ import (Picker)
    def create_session(self) -> dict:
        s = self._req("POST", f"{PICKER}/sessions", json={}).json()
        return {"id": s["id"], "picker_uri": s["pickerUri"], "polling": s.get("pollingConfig", {})}

    def get_session(self, sid: str) -> dict:
        return self._req("GET", f"{PICKER}/sessions/{sid}").json()

    def wait_for_selection(self, sid: str, timeout: float = 1800, should_stop: Callable[[], bool] = lambda: False,
                           sleep: Callable[[float], None] = time.sleep) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout and not should_stop():
            s = self.get_session(sid)
            if s.get("mediaItemsSet"):
                return True
            interval = _duration(s.get("pollingConfig", {}).get("pollInterval"), 3.0)
            sleep(max(1.0, min(interval, 10.0)))
        return False

    def picked_items(self, sid: str) -> list[dict]:
        items, token = [], None
        while True:
            params = {"sessionId": sid, "pageSize": 100, **({"pageToken": token} if token else {})}
            d = self._req("GET", f"{PICKER}/mediaItems", params=params).json()
            items += d.get("mediaItems", [])
            token = d.get("nextPageToken")
            if not token:
                return items

    def download(self, item: dict, dest_dir: Path) -> Path:
        mf = item.get("mediaFile", {})
        base = mf["baseUrl"]
        is_video = item.get("type") == "VIDEO" or str(mf.get("mimeType", "")).startswith("video/")
        name = _safe_name(mf.get("filename") or f"{item['id']}{'.mp4' if is_video else '.jpg'}")
        dest = _unique(dest_dir / name)
        with self._http.stream("GET", base + ("=dv" if is_video else "=d"),
                               headers={"Authorization": f"Bearer {self._token()}"}) as r:
            if r.status_code >= 400:
                raise GoogleError(f"download failed ({r.status_code}) for {name}")
            tmp = dest.with_suffix(dest.suffix + ".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
            tmp.replace(dest)
        return dest

    def delete_session(self, sid: str) -> None:
        try:
            self._req("DELETE", f"{PICKER}/sessions/{sid}")
        except GoogleError:
            pass

    # ------------------------------------------------------------------ upload (Library API, appendonly)
    def _album_id(self) -> str:
        st = _read(STATE_PATH) or {}
        if st.get("album_id"):
            return st["album_id"]
        a = self._req("POST", f"{LIBRARY}/albums", json={"album": {"title": ALBUM_TITLE}}).json()
        _write(STATE_PATH, {**st, "album_id": a["id"], "album_title": a.get("title", ALBUM_TITLE)})
        return a["id"]

    def upload(self, files: list[Path], progress: Callable[[float, str], None] | None = None) -> dict:
        tokens = []
        for i, f in enumerate(files):
            mime = UPLOAD_EXTS.get(f.suffix.lower())
            if not mime:
                raise GoogleError(f"{f.name}: Google Photos accepts JPEG/PNG/TIFF/MP4 uploads")
            if progress:
                progress(i / max(len(files), 1) * 0.9, f"Uploading {f.name}")
            r = self._req("POST", f"{LIBRARY}/uploads", content=f.read_bytes(),
                          headers={"Content-Type": "application/octet-stream", "X-Goog-Upload-Content-Type": mime,
                                   "X-Goog-Upload-Protocol": "raw", "X-Goog-Upload-File-Name": f.name})
            tokens.append((f, r.text.strip()))
        album = self._album_id()
        created = []
        for start in range(0, len(tokens), 50):  # batchCreate takes at most 50 items
            batch = tokens[start:start + 50]
            body = {"albumId": album, "newMediaItems": [{"description": "Edited in Charlie's Camera Studio",
                                                          "simpleMediaItem": {"uploadToken": t, "fileName": f.name}} for f, t in batch]}
            res = self._req("POST", f"{LIBRARY}/mediaItems:batchCreate", json=body).json()
            for r in res.get("newMediaItemResults", []):
                ok = r.get("status", {}).get("message") in ("Success", "OK") or "mediaItem" in r
                created.append({"ok": ok, "url": r.get("mediaItem", {}).get("productUrl"), "error": None if ok else r.get("status")})
        return {"album": (_read(STATE_PATH) or {}).get("album_title", ALBUM_TITLE), "items": created,
                "uploaded": sum(1 for c in created if c["ok"])}


# ---------------------------------------------------------------------- helpers
def _friendly_error(r: httpx.Response) -> str:
    """Turn Google's JSON errors into one actionable sentence."""
    try:
        err = r.json().get("error", {})
        msg = err.get("message", "") or r.text
    except Exception:
        msg = r.text
    if "has not been used in project" in msg or "is disabled" in msg:
        api = "Photos Picker API" if "photospicker" in msg else "Photos Library API" if "photoslibrary" in msg else "the API"
        link = re.search(r"https://console\.developers\.google\.com/\S+?(?=\s|$)", msg)
        return (f"{api} is not turned on for your Google Cloud project yet. Enable it here, wait a minute, then try again: "
                + (link.group(0).rstrip(".") if link else "https://console.cloud.google.com/apis/library"))
    if r.status_code == 403 and "insufficient" in msg.lower():
        return "Google Photos permission missing: click disconnect, then Connect again and tick every box."
    if r.status_code == 401:
        return "Google sign-in expired. Click disconnect, then Connect Google Photos again."
    return f"Google Photos error {r.status_code}: {msg[:300]}"



def _duration(v, default: float) -> float:
    m = re.match(r"^([\d.]+)s$", str(v or ""))
    return float(m.group(1)) if m else default


def _safe_name(name: str) -> str:
    name = Path(name).name
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name) or "photo.jpg"


def _unique(p: Path) -> Path:
    if not p.exists():
        return p
    for i in range(2, 10000):
        q = p.with_name(f"{p.stem} ({i}){p.suffix}")
        if not q.exists():
            return q
    raise GoogleError("too many files with the same name")


def _email_from_id_token(id_token: str | None) -> str | None:
    if not id_token:
        return None
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("email")
    except Exception:
        return None


gphotos = GooglePhotos()
