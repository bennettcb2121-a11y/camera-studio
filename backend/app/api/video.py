from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from ..config import EXPORT_DIR
from ..core import jobs
from ..core import video as V
from .common import existing_file

router = APIRouter(prefix="/api/video", tags=["video"])


@router.get("/info")
def info(path: str):
    return V.probe(existing_file(path))


@router.get("/thumb")
def thumb(path: str, t: float = 0.5, width: int = 320):
    data = V.clip_thumbnail(existing_file(path), t, width)
    if not data:
        raise HTTPException(500, "no frame")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


@router.post("/proxy")
def make_proxy(body: dict):
    src = existing_file(body["path"])

    def work(ctx: jobs.JobContext):
        ctx.progress(0.1, f"Making 720p proxy for {src.name}")
        return {"proxy": str(V.ensure_proxy(src))}

    return jobs.queue.submit("video-proxy", work).to_dict()


@router.get("/proxy")
def get_proxy(path: str):
    src = existing_file(path)
    p = V.proxy_path(src)
    if not p.exists():
        raise HTTPException(404, "proxy not built yet")
    return FileResponse(p, media_type="video/mp4")


# ---- projects
@router.get("/projects")
def projects():
    return {"projects": V.list_projects()}


@router.post("/projects")
def create_project(body: dict | None = None):
    return V.new_project((body or {}).get("name") or "Untitled reel")


@router.get("/projects/{pid}")
def get_project(pid: str):
    try:
        return V.load_project(pid)
    except KeyError:
        raise HTTPException(404, "project not found")


@router.put("/projects/{pid}")
def put_project(pid: str, body: dict):
    """Replace the whole project (the UI edits the timeline locally and saves)."""
    proj = get_project(pid)
    for k in ("name", "clips", "music", "fps"):
        if k in body:
            proj[k] = body[k]
    return V.save_project(proj)


@router.delete("/projects/{pid}")
def delete_project(pid: str):
    if not V.delete_project(pid):
        raise HTTPException(404, "project not found")
    return {"ok": True}


class ClipIn(BaseModel):
    type: str  # video | photo | title
    src: str | None = None
    start: float | None = None
    end: float | None = None
    duration: float = 4.0
    text: str = ""
    zoom: bool = True


@router.post("/projects/{pid}/clips")
def add_clip(pid: str, body: ClipIn):
    proj = get_project(pid)
    if body.type == "video":
        src = existing_file(body.src or "")
        V.ensure_proxy(src)
        c = V.add_video_clip(proj, src, body.start, body.end)
    elif body.type == "photo":
        c = V.add_photo_clip(proj, existing_file(body.src or ""), body.duration, body.zoom)
    elif body.type == "title":
        c = V.add_title_clip(proj, body.text or "Title", body.duration)
    else:
        raise HTTPException(400, "bad clip type")
    V.save_project(proj)
    return {"clip": c, "project": proj}


@router.patch("/projects/{pid}/clips/{cid}")
def patch_clip(pid: str, cid: str, body: dict):
    proj = get_project(pid)
    try:
        _, c = V.find_clip(proj, cid)
    except KeyError:
        raise HTTPException(404, "clip not found")
    for k, v in body.items():
        if k in ("id", "type", "src"):
            continue
        c[k] = v
    if c["type"] == "video":
        c["in"] = max(0.0, float(c["in"]))
        c["out"] = min(float(c.get("src_duration", c["out"])), float(c["out"]))
        if c["out"] - c["in"] < 0.1:
            c["out"] = c["in"] + 0.1
    return {"clip": c, "project": V.save_project(proj)}


@router.delete("/projects/{pid}/clips/{cid}")
def delete_clip(pid: str, cid: str):
    proj = get_project(pid)
    proj["clips"] = [c for c in proj["clips"] if c["id"] != cid]
    return V.save_project(proj)


@router.post("/projects/{pid}/reorder")
def reorder(pid: str, body: dict):
    proj = get_project(pid)
    order = body.get("order", [])
    by_id = {c["id"]: c for c in proj["clips"]}
    proj["clips"] = [by_id[i] for i in order if i in by_id] + [c for c in proj["clips"] if c["id"] not in order]
    return V.save_project(proj)


@router.post("/projects/{pid}/split")
def split(pid: str, body: dict):
    proj = get_project(pid)
    new = V.split_at(proj, float(body.get("playhead", 0.0)))
    return {"new_clip": new, "project": V.save_project(proj)}


@router.post("/projects/{pid}/shots")
def shots(pid: str, body: dict):
    proj = get_project(pid)
    cid = body.get("clip_id")

    def work(ctx: jobs.JobContext):
        ctx.progress(0.1, "Detecting scenes")
        new = V.split_into_shots(proj, cid)
        V.save_project(proj)
        return {"shots": new, "project": proj}

    return jobs.queue.submit("scene-detect", work).to_dict()


@router.post("/projects/{pid}/music")
def music(pid: str, body: dict):
    proj = get_project(pid)
    if body.get("src"):
        existing_file(body["src"])
        proj["music"] = {"src": body["src"], "volume": float(body.get("volume", 0.8)), "fade_in": float(body.get("fade_in", 1.0)),
                         "fade_out": float(body.get("fade_out", 2.0)), "keep_clip_audio": bool(body.get("keep_clip_audio", True))}
    else:
        proj["music"] = None
    return V.save_project(proj)


class RenderIn(BaseModel):
    aspect: str = "16:9"
    draft: bool = False
    name: str | None = None


@router.post("/projects/{pid}/render")
def render(pid: str, body: RenderIn):
    proj = get_project(pid)
    if body.aspect not in V.EXPORT_PRESETS:
        raise HTTPException(400, f"aspect must be one of {list(V.EXPORT_PRESETS)}")
    safe = "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in (body.name or proj.get("name") or "reel")).strip() or "reel"
    tag = body.aspect.replace(":", "x")
    out = (V.RENDER_DIR if body.draft else EXPORT_DIR) / f"{safe}_{tag}{'_draft' if body.draft else ''}.mp4"

    def work(ctx: jobs.JobContext):
        V.render_project(proj, out, body.aspect, body.draft, progress=ctx.progress)
        return {"file": str(out), "url": f"/api/video/file?path={out}"}

    return jobs.queue.submit("video-render", work).to_dict()


@router.get("/file")
def file(path: str):
    p = existing_file(path)
    if p.suffix.lower() not in (".mp4", ".mov", ".m4a", ".mp3", ".wav"):
        raise HTTPException(400, "not a media file")
    return FileResponse(p, media_type="video/mp4" if p.suffix.lower() in (".mp4", ".mov") else "audio/mp4")
