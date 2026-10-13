from __future__ import annotations

import base64
import io
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from PIL import Image

from ..config import EXPORT_DIR, PROXY_LONG_EDGE
from ..core import jobs, library
from ..core.auto import auto_settings
from ..core.images import is_raw, load_linear, proxy_cache, read_exif, resize_long_edge
from ..core.pipeline import finalize, histogram, render
from ..core.presets import list_presets, apply_preset
from ..core.settings_schema import EditSettings
from ..core.sidecar import Sidecar
from .common import existing_file, jpeg_response

router = APIRouter(prefix="/api/photos", tags=["photos"])


def _settings_from(body_settings: dict | None, sc: Sidecar) -> EditSettings:
    if body_settings is None:
        return sc.settings
    return EditSettings.model_validate(body_settings)


@router.get("/info")
def info(path: str):
    p = existing_file(path)
    sc = Sidecar(p)
    proxy, fw, fh = proxy_cache.get(p, lens_correction=sc.settings.lens_correction)
    return {"path": str(p), "name": p.name, "is_raw": is_raw(p), "exif": read_exif(p), "width": fw, "height": fh,
            "settings": sc.settings.model_dump(), "history": sc.history_summary(), "rating": sc.rating,
            "mockup_favorites": sc.data.get("mockup_favorites", [])}


class PreviewIn(BaseModel):
    path: str
    settings: dict | None = None
    size: int = PROXY_LONG_EDGE
    before: bool = False
    quality: int = 85
    with_histogram: bool = True


@router.post("/preview")
def preview(body: PreviewIn):
    p = existing_file(body.path)
    sc = Sidecar(p)
    s = EditSettings() if body.before else _settings_from(body.settings, sc)
    if body.before:  # keep the crop so before/after lines up
        s = s.merged({"crop": _settings_from(body.settings, sc).crop.model_dump()})
    proxy, fw, fh = proxy_cache.get(p, lens_correction=s.lens_correction)
    src = resize_long_edge(proxy, max(256, min(body.size, PROXY_LONG_EDGE)))
    t0 = time.time()
    lin = render(src, s, src.shape[1] / fw)
    out = finalize(lin)
    headers = {"X-Render-Ms": f"{(time.time() - t0) * 1000:.1f}", "X-Full-Size": f"{fw}x{fh}"}
    if body.with_histogram:
        headers["X-Histogram"] = json.dumps(histogram(lin))
    return jpeg_response(out, body.quality, headers)


class SettingsIn(BaseModel):
    path: str
    settings: dict
    label: str = "Edit"
    record: bool = True


@router.post("/settings")
def set_settings(body: SettingsIn):
    p = existing_file(body.path)
    sc = Sidecar(p)
    s = EditSettings.model_validate(body.settings)
    sc.set_settings(s, label=body.label, record=body.record)
    library.update_item(str(p), has_edits=0 if s.is_identity() else 1)
    return {"settings": s.model_dump(), "history": sc.history_summary()}


class PathIn(BaseModel):
    path: str


@router.post("/undo")
def undo(body: PathIn):
    sc = Sidecar(existing_file(body.path))
    s = sc.undo()
    return {"settings": s.model_dump(), "history": sc.history_summary()}


@router.post("/redo")
def redo(body: PathIn):
    sc = Sidecar(existing_file(body.path))
    s = sc.redo()
    return {"settings": s.model_dump(), "history": sc.history_summary()}


class JumpIn(PathIn):
    index: int


@router.post("/history/jump")
def jump(body: JumpIn):
    sc = Sidecar(existing_file(body.path))
    s = sc.jump(body.index)
    return {"settings": s.model_dump(), "history": sc.history_summary()}


@router.post("/reset")
def reset(body: PathIn):
    sc = Sidecar(existing_file(body.path))
    s = sc.reset()
    library.update_item(str(body.path), has_edits=0)
    return {"settings": s.model_dump(), "history": sc.history_summary()}


@router.post("/auto")
def auto(body: PathIn):
    p = existing_file(body.path)
    sc = Sidecar(p)
    proxy, fw, fh = proxy_cache.get(p)
    s = auto_settings(proxy, read_exif(p), sc.settings)
    sc.set_settings(s, label="Auto")
    library.update_item(str(p), has_edits=1)
    return {"settings": s.model_dump(), "history": sc.history_summary()}


class CopyIn(BaseModel):
    source: str
    targets: list[str]
    fields: list[str] | None = None  # None = everything except crop


@router.post("/copy-settings")
def copy_settings(body: CopyIn):
    src = Sidecar(existing_file(body.source)).settings.model_dump()
    fields = body.fields or [k for k in src if k != "crop"]
    partial = {k: src[k] for k in fields if k in src}
    done = []
    for t in body.targets:
        try:
            tp = existing_file(t)
        except HTTPException:
            continue
        sc = Sidecar(tp)
        sc.set_settings(sc.settings.merged(partial), label="Paste settings")
        library.update_item(str(tp), has_edits=1)
        done.append(str(tp))
    return {"applied": done, "fields": fields}


@router.get("/filter-strip")
def filter_strip(path: str, size: int = 160):
    """Live thumbnails of every filter applied to the current edit (crop kept)."""
    p = existing_file(path)
    sc = Sidecar(p)
    base = sc.settings
    proxy, fw, fh = proxy_cache.get(p, lens_correction=base.lens_correction)
    small = resize_long_edge(proxy, size * 2)
    scale = small.shape[1] / fw
    out = []

    def enc(img8):
        buf = io.BytesIO()
        Image.fromarray(img8).save(buf, "JPEG", quality=78)
        return base64.b64encode(buf.getvalue()).decode()

    out.append({"id": None, "name": "None", "group": "", "thumb": enc(finalize(render(small, base.merged({"lut": {"name": None}}), scale)))})
    for rec in list_presets():
        s = apply_preset(base, rec["id"])
        out.append({"id": rec["id"], "name": rec["name"], "group": rec["group"], "thumb": enc(finalize(render(small, s, scale)))})
    return {"filters": out}


class ExportIn(BaseModel):
    paths: list[str]
    format: str = "jpeg"  # jpeg | png | tiff16
    quality: int = 92
    long_edge: int | None = None
    out_dir: str | None = None
    settings: dict | None = None  # override (used by tests); otherwise each sidecar


def export_one(p: Path, fmt: str, quality: int, long_edge: int | None, out_dir: Path, settings: EditSettings | None = None) -> Path:
    sc = Sidecar(p)
    s = settings or sc.settings
    lin = load_linear(p, half_size=False, lens_correction=s.lens_correction)
    full_w = lin.shape[1]
    if long_edge:
        lin = resize_long_edge(lin, long_edge)
    # scale = this width / full width, so radius-based stages match the preview
    out = render(lin, s, lin.shape[1] / full_w)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = p.stem
    if fmt == "png":
        dest = out_dir / f"{stem}_edited.png"
        Image.fromarray(finalize(out, 8)).save(dest, compress_level=6)
    elif fmt in ("tiff16", "tiff"):
        import tifffile

        dest = out_dir / f"{stem}_edited.tif"
        tifffile.imwrite(dest, finalize(out, 16), photometric="rgb", compression="zlib")
    else:
        dest = out_dir / f"{stem}_edited.jpg"
        Image.fromarray(finalize(out, 8)).save(dest, quality=quality, subsampling=0, optimize=True)
    return dest


@router.post("/export")
def export(body: ExportIn):
    paths = [existing_file(p) for p in body.paths]
    out_dir = Path(body.out_dir).expanduser() if body.out_dir else EXPORT_DIR
    override = EditSettings.model_validate(body.settings) if body.settings else None

    def work(ctx: jobs.JobContext):
        done = []
        for i, p in enumerate(paths):
            if ctx.cancelled:
                break
            ctx.progress(i / len(paths), f"Exporting {p.name}")
            done.append(str(export_one(p, body.format, body.quality, body.long_edge, out_dir, override)))
        return {"files": done, "out_dir": str(out_dir)}

    job = jobs.queue.submit("export", work)
    return job.to_dict()
