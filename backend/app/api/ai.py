from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import AI_BACKEND, CLAUDE_MODEL, anthropic_api_key
from ..core import jobs, library
from ..core.settings_schema import EditSettings
from ..core.sidecar import Sidecar
from ..core.video import contact_sheet, load_project, save_project
from ..services.ai import MODES, AIService, EditSession, load_preferences, log_pick
from .common import existing_file

router = APIRouter(prefix="/api/ai", tags=["ai"])

# A module-level service so tests can swap the runner.
service = AIService()

NOT_READY = ("The AI assistant uses your Claude account. Sign in once by running "
             "`uv run python -m backend.app.claude_login` in the app folder (or restart with ./run.sh), "
             "then click Retry. Alternatively put ANTHROPIC_API_KEY in .env.")


@router.get("/status")
def status(refresh: bool = False):
    from ..claude_login import auth_status

    if refresh:
        auth_status(max_age=0)
        service.reset()
    login = auth_status()
    return {"available": service.available, "backend": getattr(service.runner, "name", "?"), "configured": AI_BACKEND,
            "model": CLAUDE_MODEL, "claude_logged_in": bool(login.get("loggedIn")), "account": login.get("email"),
            "key_set": anthropic_api_key() is not None, "picks_logged": len(load_preferences().get("picks", []))}


class RunIn(BaseModel):
    path: str
    mode: str
    note: str = ""
    n: int = 5


def _run_one(path, mode: str, note: str, n: int, ctx: jobs.JobContext, frac0: float = 0.0, span: float = 1.0) -> dict:
    """Run one mode on one photo; every committed edit is published on the job so the UI can re-render live."""
    edits = {"n": 0}

    def on_change(settings: EditSettings, label: str):
        edits["n"] += 1
        ctx.job.live = {"path": str(path), "settings": settings.model_dump(), "label": label, "seq": edits["n"]}
        ctx.progress(frac0 + span * min(0.9, 0.2 + 0.15 * edits["n"]), f"{path.name}: {label}")

    ctx.progress(frac0 + 0.05 * span, f"{path.name}: Claude is looking at the photo")
    session = EditSession(path, on_change=on_change)
    res = service.run_mode(mode, session, note, n=max(4, min(6, n)),
                           progress=lambda f, m: ctx.progress(frac0 + span * f, m))
    library.update_item(str(path), has_edits=0 if session.settings.is_identity() else 1)
    res["history"] = session.sidecar.history_summary()
    res["path"] = str(path)
    return res


@router.post("/run")
def run(body: RunIn):
    if body.mode not in MODES:
        raise HTTPException(400, f"mode must be one of {MODES}")
    if body.mode == "ask" and not body.note.strip():
        raise HTTPException(400, "Type what you'd like Claude to do")
    if not service.available:
        raise HTTPException(409, NOT_READY)
    p = existing_file(body.path)
    return jobs.queue.submit(f"ai-{body.mode}", lambda ctx: _run_one(p, body.mode, body.note, body.n, ctx)).to_dict()


class BatchIn(BaseModel):
    paths: list[str]
    mode: str = "auto_edit"  # auto_edit | ask
    note: str = ""


@router.post("/batch")
def batch(body: BatchIn):
    """Run an AI edit over many photos (one after the other, in one background job)."""
    if body.mode not in ("auto_edit", "ask"):
        raise HTTPException(400, "batch mode must be auto_edit or ask")
    if body.mode == "ask" and not body.note.strip():
        raise HTTPException(400, "Type what you'd like Claude to do")
    if not service.available:
        raise HTTPException(409, NOT_READY)
    paths = [existing_file(p) for p in body.paths]

    def work(ctx: jobs.JobContext):
        out = []
        for i, p in enumerate(paths):
            if ctx.cancelled:
                break
            try:
                res = _run_one(p, body.mode, body.note, 5, ctx, i / len(paths), 1 / len(paths))
                out.append({"path": str(p), "ok": True, "text": res["text"]})
            except Exception as e:  # one bad photo must not stop the batch
                out.append({"path": str(p), "ok": False, "text": str(e)})
        return {"results": out}

    return jobs.queue.submit("ai-batch", work).to_dict()


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
        raise HTTPException(409, NOT_READY)
    src = existing_file(body.clip_src)
    proj = load_project(body.project_id)

    def work(ctx: jobs.JobContext):
        from ..core.video import add_title_clip, add_video_clip, probe

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
