from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from PIL import Image

from ..config import EXPORT_DIR, MOCKUPS_DIR
from ..core import jobs
from ..core.images import proxy_cache, resize_long_edge
from ..core.mockups import get_template, list_templates, render_mockup, save_template
from ..core.pipeline import finalize, render
from ..core.settings_schema import EditSettings
from ..core.sidecar import Sidecar
from .common import existing_file, jpeg_response

router = APIRouter(prefix="/api/mockups", tags=["mockups"])


def _edited(p: Path, long_edge: int = 1400, settings: dict | None = None):
    sc = Sidecar(p)
    s = EditSettings.model_validate(settings) if settings else sc.settings
    proxy, fw, fh = proxy_cache.get(p, lens_correction=s.lens_correction)
    small = resize_long_edge(proxy, long_edge)
    return finalize(render(small, s, small.shape[1] / fw))


@router.get("/templates")
def templates():
    return {"templates": list_templates()}


@router.get("/background")
def background(template_id: str):
    t = get_template(template_id)
    if not t:
        raise HTTPException(404, "template not found")
    return FileResponse(MOCKUPS_DIR / template_id / "background.jpg", media_type="image/jpeg")


class RenderIn(BaseModel):
    template_id: str
    paths: list[str]  # first = current photo; extra ones fill multi-slot templates
    settings: dict | None = None  # live (uncommitted) settings for the first photo
    long_edge: int = 1400


@router.post("/render")
def render_one(body: RenderIn):
    t = get_template(body.template_id)
    if not t:
        raise HTTPException(404, "template not found")
    photos = []
    for i, p in enumerate(body.paths):
        photos.append(_edited(existing_file(p), body.long_edge, body.settings if i == 0 else None))
    if not photos:
        raise HTTPException(400, "no photos")
    out = render_mockup(t, photos)
    out = resize_long_edge(out, body.long_edge)
    return jpeg_response(out, 86)


class FavIn(BaseModel):
    path: str
    template_id: str
    favorite: bool


@router.post("/favorite")
def favorite(body: FavIn):
    sc = Sidecar(existing_file(body.path))
    favs = set(sc.data.get("mockup_favorites", []))
    (favs.add if body.favorite else favs.discard)(body.template_id)
    sc.data["mockup_favorites"] = sorted(favs)
    sc.save()
    return {"mockup_favorites": sc.data["mockup_favorites"]}


class ExportAllIn(BaseModel):
    path: str
    fill_paths: list[str] = []
    template_ids: list[str] | None = None  # None = all
    out_dir: str | None = None


@router.post("/export-all")
def export_all(body: ExportAllIn):
    p = existing_file(body.path)
    fills = [existing_file(f) for f in body.fill_paths]
    ids = body.template_ids or [t["id"] for t in list_templates()]
    out_dir = Path(body.out_dir).expanduser() if body.out_dir else EXPORT_DIR

    def work(ctx: jobs.JobContext):
        photos = [_edited(p, 2000)] + [_edited(f, 2000) for f in fills]
        out_dir.mkdir(parents=True, exist_ok=True)
        zpath = out_dir / f"{p.stem}_mockups.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for i, tid in enumerate(ids):
                if ctx.cancelled:
                    break
                t = get_template(tid)
                if not t:
                    continue
                ctx.progress(i / len(ids), f"Rendering {t['name']}")
                img = render_mockup(t, photos)
                buf = io.BytesIO()
                Image.fromarray(img).save(buf, "JPEG", quality=90)
                z.writestr(f"{p.stem}_{tid}.jpg", buf.getvalue())
        return {"zip": str(zpath)}

    return jobs.queue.submit("mockups-zip", work).to_dict()


class TemplateIn(BaseModel):
    id: str | None = None
    name: str
    slots: list[dict]  # [{"corners": [[x,y]x4]}] in background pixels
    blend: dict | None = None
    background_path: str | None = None  # any image file; copied into the template folder


@router.post("/templates")
def create_template(body: TemplateIn):
    tid = body.id or re.sub(r"[^a-z0-9]+", "-", body.name.lower()).strip("-") or "template"
    bg = None
    if body.background_path:
        bg = existing_file(body.background_path)
    elif not (MOCKUPS_DIR / tid / "background.jpg").exists():
        raise HTTPException(400, "background_path required for a new template")
    for s in body.slots:
        c = s.get("corners", [])
        if len(c) != 4:
            raise HTTPException(400, "each slot needs 4 corners")
    return save_template(tid, body.name, body.slots, body.blend, bg)


@router.post("/upload-background")
async def upload_background(file: UploadFile = File(...)):
    """Save an uploaded image to the mockups folder so it can be used as a background."""
    data = await file.read()
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        raise HTTPException(400, "not an image")
    name = re.sub(r"[^a-z0-9]+", "-", Path(file.filename or "upload").stem.lower()).strip("-") or "upload"
    dest = MOCKUPS_DIR / "_uploads"
    dest.mkdir(exist_ok=True)
    path = dest / f"{name}.jpg"
    im.save(path, quality=92)
    return {"path": str(path), "width": im.width, "height": im.height}


@router.delete("/templates/{tid}")
def delete_template(tid: str):
    d = MOCKUPS_DIR / tid
    if not d.is_dir():
        raise HTTPException(404, "template not found")
    for f in d.iterdir():
        f.unlink()
    d.rmdir()
    return {"ok": True}
