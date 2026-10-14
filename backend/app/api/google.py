from __future__ import annotations

import datetime as dt
import html
import json
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from ..config import EXPORT_DIR, GOOGLE_IMPORT_DIR
from ..core import jobs, library
from ..services.google_photos import GoogleError, gphotos
from .common import existing_file

router = APIRouter(prefix="/api/google", tags=["google"])


def _err(e: Exception):
    raise HTTPException(400, str(e))


@router.get("/status")
def status():
    return {**gphotos.status(), "import_dir": str(GOOGLE_IMPORT_DIR)}


@router.post("/client")
async def upload_client(file: UploadFile = File(...)):
    """The OAuth client JSON downloaded from Google Cloud ('Desktop app' type)."""
    try:
        return gphotos.save_client((await file.read()).decode("utf-8", "replace"))
    except (GoogleError, ValueError) as e:
        _err(e)


@router.get("/connect")
def connect(request: Request):
    # loopback redirect to this server; Google allows any port for Desktop-app clients
    redirect_uri = f"http://127.0.0.1:{request.url.port or 80}/api/google/callback"
    try:
        return RedirectResponse(gphotos.auth_url(redirect_uri))
    except GoogleError as e:
        _err(e)


@router.get("/callback", response_class=HTMLResponse)
def callback(code: str | None = None, state: str | None = None, error: str | None = None):
    if error or not code or not state:
        msg = f"Google sign-in was cancelled ({html.escape(error or 'no code')})."
        if error == "access_denied":
            msg += (" If Google said “Access blocked”, add your Gmail under Google Cloud → Google Auth Platform → "
                    "Audience → Test users, then click Connect again.")
    else:
        try:
            r = gphotos.finish_auth(code, state)
            msg = f"Connected to Google Photos{(' as ' + html.escape(r['email'])) if r.get('email') else ''}."
            if r.get("missing_scopes"):
                msg += " Some permissions were not granted — please connect again and tick every box."
        except GoogleError as e:
            msg = html.escape(str(e))
    return f"""<!doctype html><meta charset=utf-8><title>Google Photos</title>
<body style="background:#0f0f10;color:#e6e6e6;font-family:system-ui;display:grid;place-items:center;height:100vh;margin:0">
<div style="text-align:center"><p style="font-size:18px">{msg}</p><p style="color:#888">You can close this tab and go back to the app.</p>
<p><a style="color:#60a5fa" href="/">Back to Charlie's Camera Studio</a></p></div>
<script>try{{window.opener&&window.opener.postMessage('google-connected','*');setTimeout(()=>window.close(),1500)}}catch(e){{}}</script>"""


@router.post("/disconnect")
def disconnect():
    gphotos.disconnect()
    return gphotos.status()


@router.post("/import")
def start_import():
    """Open a Google Photos Picker session. The UI opens picker_uri; this job waits for
    Charlie to finish picking, downloads the originals and adds the folder to the library."""
    try:
        session = gphotos.create_session()
    except GoogleError as e:
        _err(e)
    dest = GOOGLE_IMPORT_DIR / dt.datetime.now().strftime("%Y-%m-%d %H.%M")

    def work(ctx: jobs.JobContext):
        ctx.progress(0.02, "Waiting for you to pick photos in Google Photos…")
        if not gphotos.wait_for_selection(session["id"], should_stop=lambda: ctx.cancelled):
            gphotos.delete_session(session["id"])
            return {"imported": 0, "folder": None, "message": "No selection made"}
        items = gphotos.picked_items(session["id"])
        dest.mkdir(parents=True, exist_ok=True)
        files = []
        for i, it in enumerate(items):
            if ctx.cancelled:
                break
            name = it.get("mediaFile", {}).get("filename", "item")
            ctx.progress(0.1 + 0.85 * i / max(len(items), 1), f"Downloading {name} ({i + 1}/{len(items)})")
            files.append(str(gphotos.download(it, dest)))
        gphotos.delete_session(session["id"])
        n = library.scan_folder(dest) if files else 0
        # the library keys folders by resolved path, so report that one
        return {"imported": len(files), "indexed": n, "folder": str(dest.resolve()), "files": files}

    job = jobs.queue.submit("google-import", work)
    return {"job": job.to_dict(), "picker_uri": session["picker_uri"]}


class UploadIn(BaseModel):
    paths: list[str]
    quality: int = 92


@router.post("/upload")
def upload(body: UploadIn):
    """Export each photo with its edits (full-res JPEG) and upload it to the
    "Charlie's Camera Studio" album. Video files from exports/ are uploaded as they are."""
    if not gphotos.status()["connected"]:
        raise HTTPException(409, "Connect Google Photos first.")
    paths = [existing_file(p) for p in body.paths]

    def work(ctx: jobs.JobContext):
        from .photos import export_one

        out_dir = EXPORT_DIR / "google-photos"
        files = []
        for i, p in enumerate(paths):
            if p.suffix.lower() in (".mp4", ".mov"):
                files.append(p)
                continue
            ctx.progress(0.4 * i / len(paths), f"Rendering {p.name}")
            files.append(export_one(p, "jpeg", body.quality, None, out_dir))
        res = gphotos.upload(files, progress=lambda f, m: ctx.progress(0.4 + 0.6 * f, m))
        return res

    return jobs.queue.submit("google-upload", work).to_dict()
