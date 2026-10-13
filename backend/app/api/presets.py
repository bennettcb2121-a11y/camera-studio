from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..core import library
from ..core.luts import list_luts
from ..core.presets import apply_preset, delete_user_preset, get_preset, list_presets, save_user_preset
from ..core.settings_schema import EditSettings
from ..core.sidecar import Sidecar
from .common import existing_file

router = APIRouter(prefix="/api/presets", tags=["presets"])


@router.get("")
def presets():
    return {"presets": list_presets(), "luts": list_luts()}


class ApplyIn(BaseModel):
    path: str
    preset_id: str | None
    strength: float | None = None


@router.post("/apply")
def apply(body: ApplyIn):
    p = existing_file(body.path)
    sc = Sidecar(p)
    if body.preset_id is None:
        s = sc.settings.merged({"lut": {"name": None, "strength": 1.0}})
        sc.set_settings(s, label="Remove filter")
    else:
        try:
            s = apply_preset(sc.settings, body.preset_id, body.strength)
        except KeyError:
            raise HTTPException(404, "preset not found")
        sc.set_settings(s, label=f"Filter: {get_preset(body.preset_id)['name']}")
    library.update_item(str(p), has_edits=0 if s.is_identity() else 1)
    return {"settings": s.model_dump(), "history": sc.history_summary()}


class SaveIn(BaseModel):
    name: str
    path: str | None = None
    settings: dict | None = None
    notes: str = ""
    group: str = "User"


@router.post("/save")
def save(body: SaveIn):
    if body.settings is not None:
        s = EditSettings.model_validate(body.settings)
    elif body.path:
        s = Sidecar(existing_file(body.path)).settings
    else:
        raise HTTPException(400, "path or settings required")
    return save_user_preset(body.name, s, body.notes, group=body.group)


@router.delete("/{pid}")
def delete(pid: str):
    if not delete_user_preset(pid):
        raise HTTPException(404, "only user presets can be deleted")
    return {"ok": True}
