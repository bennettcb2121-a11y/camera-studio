from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import CLAUDE_MODEL, anthropic_api_key
from ..core import jobs, library
from ..core.settings_schema import EditSettings
from ..core.sidecar import Sidecar
from ..core.video import contact_sheet, load_project, save_project
from ..services.ai import MODES, AIService, EditSession, load_preferences, log_pick
from .common import existing_file

router = APIRouter(prefix="/api/ai", tags=["ai"])

# A module-level service so tests can swap in a fake client.
service = AIService()


@router.get("/status")
def status():
    return {"available": service.available, "model": CLAUDE_MODEL, "key_set": anthropic_api_key() is not None,
            "picks_logged": len(load_preferences().get("picks", []))}


class RunIn(BaseModel):
    path: str
    mode: str
    note: str = ""
    n: int = 5


@router.post("/run")
def run(body: RunIn):
    if body.mode not in MODES:
        raise HTTPException(400, f"mode must be one of {MODES}")
    if not service.available:
        raise HTTPException(409, "No ANTHROPIC_API_KEY set. Add it to .env (see .env.example) and restart.")
    p = existing_file(body.path)

    def work(ctx: jobs.JobContext):
        ctx.progress(0.05, "Preparing preview")
        session = EditSession(p)
        if body.mode == "critique":
            res = service.critique(session, body.note)
        elif body.mode == "auto_edit":
            res = service.auto_edit(session, body.note)
        elif body.mode == "research":
            res = service.research(session, body.note)
        else:
            res = service.best_of_n(session, body.note, n=max(4, min(6, body.n)), progress=ctx.progress)
        library.update_item(str(p), has_edits=0 if session.settings.is_identity() else 1)
        res["history"] = session.sidecar.history_summary()
        return res

    return jobs.queue.submit(f"ai-{body.mode}", work).to_dict()


class PickIn(BaseModel):
    path: str
    chosen: dict  # {"name", "settings"}
    rejected: list[dict] = []


@router.post("/pick")
def pick(body: PickIn):
    p = existing_file(body.path)
    sc = Sidecar(p)
    s = EditSettings.model_validate(body.chosen.get("settings") or {})
    sc.set_settings(s, label=f"AI: Best-of-N pick '{body.chosen.get('name', '')}'", source="ai")
    library.update_item(str(p), has_edits=1)
    prefs = log_pick({"name": body.chosen.get("name"), "settings": s.model_dump()},
                     [{"name": r.get("name"), "settings": r.get("settings")} for r in body.rejected], str(p))
    return {"settings": s.model_dump(), "history": sc.history_summary(), "picks_logged": len(prefs["picks"])}


@router.get("/preferences")
def preferences():
    return load_preferences()


class ReelIn(BaseModel):
    project_id: str
    clip_src: str  # source video to cut from
    target_seconds: int = 30
    note: str = ""
    replace: bool = True  # replace the timeline with the cut list


@router.post("/reel")
def reel(body: ReelIn):
    if not service.available:
        raise HTTPException(409, "No ANTHROPIC_API_KEY set.")
    src = existing_file(body.clip_src)
    proj = load_project(body.project_id)

    def work(ctx: jobs.JobContext):
        from ..core.video import add_video_clip, add_title_clip, probe

        ctx.progress(0.1, "Sampling frames")
        sheet, ts = contact_sheet(src)
        ctx.progress(0.4, "Asking Claude for a cut list")
        cut = service.reel_cut_list(sheet, ts, src.name, body.target_seconds, body.note)
        dur = probe(src)["duration"]
        if body.replace:
            proj["clips"] = []
        if cut.get("title"):
            add_title_clip(proj, cut["title"], duration=2.5)
        for seg in cut.get("segments", []):
            s, e = max(0.0, float(seg["start"])), min(dur, float(seg["end"]))
            if e - s < 0.3:
                continue
            c = add_video_clip(proj, src, s, e)
            c["speed"] = float(seg.get("speed", 1.0) or 1.0)
        save_project(proj)
        return {"project": proj, "cut_list": cut}

    return jobs.queue.submit("ai-reel", work).to_dict()
