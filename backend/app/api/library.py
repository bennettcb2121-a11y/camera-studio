from __future__ import annotations

import platform
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..core import library
from ..core.images import ensure_thumbnail
from ..core.sidecar import Sidecar
from ..core.video import clip_thumbnail
from .common import existing_file

router = APIRouter(prefix="/api/library", tags=["library"])


class FolderIn(BaseModel):
    path: str
    recursive: bool = True


@router.get("/folders")
def folders():
    return {"folders": library.list_folders()}


@router.post("/folders")
def add_folder(body: FolderIn):
    p = Path(body.path).expanduser()
    if not p.is_dir():
        raise HTTPException(404, f"not a folder: {body.path}")
    n = library.scan_folder(p, body.recursive)
    return {"folder": str(p.resolve()), "items": n}


@router.delete("/folders")
def remove_folder(path: str):
    library.remove_folder(path)
    return {"ok": True}


@router.post("/rescan")
def rescan():
    total = 0
    for f in library.list_folders():
        if Path(f).is_dir():
            total += library.scan_folder(Path(f))
    return {"items": total}


@router.get("/items")
def items(folder: str | None = None, kind: str | None = None, min_rating: int = 0, sort: str = "name"):
    return {"items": library.list_items(folder, kind, min_rating, sort)}


@router.get("/thumb")
def thumb(path: str, t: float = 0.5):
    p = existing_file(path)
    if library.kind_of(p) == "video":
        data = clip_thumbnail(p, t)
        if not data:
            raise HTTPException(500, "could not extract a frame")
        from fastapi.responses import Response

        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})
    try:
        tp = ensure_thumbnail(p)
    except Exception as e:
        raise HTTPException(500, f"thumbnail failed: {e}")
    return FileResponse(tp, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


class RatingIn(BaseModel):
    path: str
    rating: int


@router.post("/rating")
def rating(body: RatingIn):
    p = existing_file(body.path)
    sc = Sidecar(p)
    sc.rating = body.rating
    library.update_item(str(p), rating=sc.rating)
    return {"rating": sc.rating}


@router.get("/browse")
def browse(path: str | None = None):
    """Simple server-side folder browser for the in-app picker."""
    base = Path(path).expanduser() if path else Path.home()
    if not base.is_dir():
        raise HTTPException(404, "not a folder")
    dirs, counts = [], {}
    try:
        for c in sorted(base.iterdir(), key=lambda x: x.name.lower()):
            if c.name.startswith("."):
                continue
            if c.is_dir():
                dirs.append(str(c))
            elif c.is_file():
                k = library.kind_of(c)
                if k:
                    counts[k] = counts.get(k, 0) + 1
    except PermissionError:
        pass
    return {"path": str(base.resolve()), "parent": str(base.resolve().parent), "dirs": dirs, "media": counts}


@router.post("/pick-folder")
def pick_folder():
    """Native folder dialog (macOS: AppleScript, Windows: PowerShell). Returns null if cancelled."""
    sysname = platform.system()
    try:
        if sysname == "Darwin":
            r = subprocess.run(["osascript", "-e", 'POSIX path of (choose folder with prompt "Choose a photo/video folder")'],
                               capture_output=True, text=True, timeout=300)
            out = r.stdout.strip()
            return {"path": out.rstrip("/") or None}
        if sysname == "Windows":
            ps = ("Add-Type -AssemblyName System.Windows.Forms; $d = New-Object System.Windows.Forms.FolderBrowserDialog; "
                  "if ($d.ShowDialog() -eq 'OK') { $d.SelectedPath }")
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=300)
            return {"path": r.stdout.strip() or None}
    except Exception:
        pass
    return {"path": None}
