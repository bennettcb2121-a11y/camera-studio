"""AI assistant: Critique, Auto-edit, Research and Best-of-N on top of the Claude API.

Every request carries a ~1500px JPEG preview, EXIF, histogram stats, current settings,
the filter list and the user's taste profile (logged Best-of-N picks). Claude gets
tools that mutate the sidecar through the normal edit path, so every AI change shows
up in the undo history with source="ai".

The Anthropic client is injectable (`AIService(client=...)`) so tests run against a
scripted fake when no ANTHROPIC_API_KEY is set.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw

from ..config import AI_PREFERENCES_PATH, AI_PREVIEW_LONG_EDGE, CLAUDE_MODEL, anthropic_api_key
from ..core import mockups as mockup_core
from ..core import presets as preset_core
from ..core.images import proxy_cache, read_exif, resize_long_edge
from ..core.pipeline import crop_rect_for_aspect, finalize, histogram, render
from ..core.settings_schema import ASPECT_PRESETS, SLIDER_RANGES, EditSettings
from ..core.sidecar import Sidecar

log = logging.getLogger(__name__)

MODES = ("critique", "auto_edit", "research", "best_of_n")

ADJ_PROPS = {k: {"type": "number", "minimum": lo, "maximum": hi} for k, (lo, hi) in SLIDER_RANGES.items() if "." not in k}
ADJ_PROPS["wb"] = {"type": "object", "properties": {"temp": {"type": "number", "minimum": -100, "maximum": 100},
                                                    "tint": {"type": "number", "minimum": -100, "maximum": 100}}}
ADJ_PROPS["curve"] = {"type": "array", "description": "Tone curve points [[x,y],...] in 0..1, must include [0,0] and [1,1]",
                      "items": {"type": "array", "items": {"type": "number"}}}
ADJ_PROPS["hsl"] = {"type": "object", "description": "8 bands: red, orange, yellow, green, aqua, blue, purple, magenta",
                    "properties": {k: {"type": "array", "items": {"type": "number"}, "minItems": 8, "maxItems": 8} for k in ("hue", "sat", "lum")}}
ADJ_PROPS["lut"] = {"type": "object", "properties": {"name": {"type": ["string", "null"]}, "strength": {"type": "number", "minimum": 0, "maximum": 1}}}

TOOLS: list[dict] = [
    {"name": "set_adjustments", "description": "Change one or more sliders. Values are absolute (not deltas) and replace the "
     "current value. Omit sliders you do not want to change. The reply contains the new histogram stats.",
     "input_schema": {"type": "object", "properties": {"adjustments": {"type": "object", "properties": ADJ_PROPS},
                                                       "reason": {"type": "string", "description": "One sentence shown to the user"}},
                      "required": ["adjustments", "reason"]}},
    {"name": "apply_filter", "description": "Apply one of the built-in or user filters by id, with optional LUT strength 0..1.",
     "input_schema": {"type": "object", "properties": {"preset_id": {"type": "string"}, "strength": {"type": "number", "minimum": 0, "maximum": 1},
                                                       "reason": {"type": "string"}}, "required": ["preset_id", "reason"]}},
    {"name": "crop", "description": "Crop/straighten. aspect is one of free, original, 1:1, 4:5, 9:16, 3:2, 16:9. x,y,w,h are "
     "fractions of the image (omit for the largest centred crop of that aspect). rotate is degrees.",
     "input_schema": {"type": "object", "properties": {"aspect": {"type": "string", "enum": list(ASPECT_PRESETS)},
                                                       "x": {"type": "number"}, "y": {"type": "number"}, "w": {"type": "number"},
                                                       "h": {"type": "number"}, "rotate": {"type": "number"}, "reason": {"type": "string"}},
                      "required": ["aspect", "reason"]}},
    {"name": "suggest_mockups", "description": "Recommend mockup templates that suit this photo.",
     "input_schema": {"type": "object", "properties": {"template_ids": {"type": "array", "items": {"type": "string"}}, "reason": {"type": "string"}},
                      "required": ["template_ids", "reason"]}},
    {"name": "save_preset", "description": "Save the given settings as a reusable user preset with notes and sources.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}, "notes": {"type": "string"},
                                                       "sources": {"type": "array", "items": {"type": "string"}},
                                                       "settings": {"type": "object", "properties": ADJ_PROPS,
                                                                    "description": "Omit to save the photo's current settings"}},
                      "required": ["name", "notes"]}},
]

PROPOSE_TOOL = {"name": "propose_variants", "description": "Propose several clearly different edits of the photo.",
                "input_schema": {"type": "object", "properties": {"variants": {"type": "array", "minItems": 3, "maxItems": 6, "items": {
                    "type": "object", "properties": {"name": {"type": "string"}, "rationale": {"type": "string"},
                                                     "adjustments": {"type": "object", "properties": ADJ_PROPS},
                                                     "preset_id": {"type": ["string", "null"]}, "strength": {"type": "number"}},
                    "required": ["name", "rationale", "adjustments"]}}}, "required": ["variants"]}}
RANK_TOOL = {"name": "rank_variants", "description": "Rank the variants shown in the comparison image, best first.",
             "input_schema": {"type": "object", "properties": {"ranking": {"type": "array", "items": {"type": "object", "properties": {
                 "index": {"type": "integer"}, "verdict": {"type": "string"}}, "required": ["index", "verdict"]}},
                 "summary": {"type": "string"}}, "required": ["ranking", "summary"]}}

WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 5}

SYSTEM = """You are the editing assistant inside Charlie's Camera Studio, a personal photo editor for a Sony a6000.
You see a preview JPEG of the current edit, the EXIF, histogram statistics, the current slider values, the list of
available filters and Charlie's taste profile. Sliders: exposure in EV (-5..5); contrast, highlights, shadows,
whites, blacks, vibrance, saturation, clarity, vignette in -100..100; clahe, denoise, sharpen in 0..100;
wb.temp (- cooler, + warmer) and wb.tint (- green, + magenta) in -100..100 relative to the camera white balance.
Be concrete and brief; speak to Charlie directly. Favour natural, believable results unless asked otherwise."""

MODE_PROMPTS = {
    "critique": "Mode: CRITIQUE. Explain the photo's strengths and the issues you see (exposure, colour, composition, "
                "focus/noise, crop) and what you would change, as a short bulleted list. Do NOT call any editing tools.",
    "auto_edit": "Mode: AUTO-EDIT. Improve the photo using the tools (set_adjustments, apply_filter, crop). Make the edit in "
                 "one to three tool calls, check the returned histogram stats, then finish with a short summary where each "
                 "change is explained in one line.",
    "research": "Mode: RESEARCH. First use web_search (max 5 searches) to learn how photographers typically edit this "
                "genre and lighting situation (think: subject type, time of day, light quality). Then apply a fitting edit "
                "with set_adjustments/apply_filter and finally call save_preset with a descriptive name, notes that "
                "summarise what you learned, and the source URLs. End with a short summary.",
    "best_of_n": "Mode: BEST-OF-N. Call propose_variants with 4 to 6 genuinely different edits (different moods, not "
                 "tiny variations). Each must be a complete set of absolute slider values.",
}


# --------------------------------------------------------------------------- preferences (taste profile)
def load_preferences() -> dict:
    if AI_PREFERENCES_PATH.exists():
        try:
            return json.loads(AI_PREFERENCES_PATH.read_text())
        except Exception:
            pass
    return {"picks": []}


def log_pick(chosen: dict, rejected: list[dict], photo: str) -> dict:
    prefs = load_preferences()
    prefs["picks"].append({"ts": time.time(), "photo": Path(photo).name, "chosen": chosen, "rejected": rejected})
    prefs["picks"] = prefs["picks"][-100:]
    AI_PREFERENCES_PATH.write_text(json.dumps(prefs, indent=1))
    return prefs


def taste_profile_text(prefs: dict | None = None) -> str:
    prefs = prefs or load_preferences()
    picks = prefs.get("picks", [])[-20:]
    if not picks:
        return "No Best-of-N picks logged yet."
    lines = []
    for p in picks:
        ch = p["chosen"]
        rej = ", ".join(r.get("name", "?") for r in p.get("rejected", []))
        lines.append(f"- picked \"{ch.get('name')}\" ({_summ(ch.get('settings', {}))}) over: {rej}")
    return "Charlie's recent Best-of-N picks (learn his taste from these):\n" + "\n".join(lines)


def _summ(s: dict) -> str:
    keys = ["exposure", "contrast", "highlights", "shadows", "vibrance", "saturation", "clarity", "vignette"]
    parts = [f"{k}={s[k]}" for k in keys if k in s and s[k]]
    if s.get("wb"):
        parts.append(f"wb={s['wb']}")
    if s.get("lut", {}).get("name"):
        parts.append(f"lut={s['lut']['name']}@{s['lut'].get('strength', 1)}")
    return ", ".join(parts) or "neutral"


# --------------------------------------------------------------------------- helpers
def _jpeg_b64(rgb8: np.ndarray, quality: int = 85) -> str:
    buf = io.BytesIO()
    Image.fromarray(rgb8).save(buf, "JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode()


def _image_block(b64: str) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}}


def _settings_delta(prev: EditSettings, new: EditSettings) -> dict:
    a, b = prev.model_dump(), new.model_dump()
    return {k: b[k] for k in b if a[k] != b[k]}


class EditSession:
    """Holds the photo, its proxy and the live settings while Claude works."""

    def __init__(self, path: Path, on_change: Callable[[EditSettings, str], None] | None = None):
        self.path = Path(path)
        self.sidecar = Sidecar(self.path)
        self.settings = self.sidecar.settings
        self.exif = read_exif(self.path)
        self.proxy, self.full_w, self.full_h = proxy_cache.get(self.path, lens_correction=self.settings.lens_correction)
        self.on_change = on_change
        self.actions: list[dict] = []

    def preview(self, settings: EditSettings | None = None, long_edge: int = AI_PREVIEW_LONG_EDGE) -> np.ndarray:
        s = settings or self.settings
        small = resize_long_edge(self.proxy, long_edge)
        return finalize(render(small, s, small.shape[1] / self.full_w))

    def stats(self, settings: EditSettings | None = None) -> dict:
        s = settings or self.settings
        small = resize_long_edge(self.proxy, 512)
        return histogram(render(small, s, small.shape[1] / self.full_w))["stats"]

    def commit(self, new: EditSettings, label: str) -> None:
        delta = _settings_delta(self.settings, new)
        self.settings = new
        self.sidecar.set_settings(new, label=f"AI: {label}", source="ai")
        self.actions.append({"label": label, "delta": delta})
        if self.on_change:
            self.on_change(new, label)

    def context_blocks(self) -> list[dict]:
        filters = [{"id": p["id"], "name": p["name"], "group": p["group"], "description": p.get("description", "")}
                   for p in preset_core.list_presets()]
        templates = [{"id": t["id"], "name": t["name"]} for t in mockup_core.list_templates()]
        info = {
            "file": self.path.name, "exif": self.exif, "full_size": [self.full_w, self.full_h],
            "histogram_stats": self.stats(), "current_settings": self.settings.model_dump(),
            "filters": filters, "mockup_templates": templates,
        }
        return [_image_block(_jpeg_b64(self.preview())),
                {"type": "text", "text": "Photo context (JSON):\n" + json.dumps(info, indent=1)},
                {"type": "text", "text": taste_profile_text()}]

    # ---- tool execution
    def run_tool(self, name: str, inp: dict) -> dict:
        try:
            if name == "set_adjustments":
                new = self.settings.merged(inp.get("adjustments") or {})
                self.commit(new, inp.get("reason") or "adjustments")
                return {"ok": True, "histogram_stats": self.stats(), "settings": new.model_dump()}
            if name == "apply_filter":
                new = preset_core.apply_preset(self.settings, inp["preset_id"], inp.get("strength"))
                self.commit(new, f"filter {inp['preset_id']}")
                return {"ok": True, "histogram_stats": self.stats()}
            if name == "crop":
                rect = crop_rect_for_aspect(self.full_w, self.full_h, inp.get("aspect", "free"))
                for k in ("x", "y", "w", "h"):
                    if inp.get(k) is not None:
                        rect[k] = float(inp[k])
                crop = {**rect, "aspect": inp.get("aspect", "free"), "rotate": float(inp.get("rotate", 0.0))}
                new = self.settings.merged({"crop": crop})
                self.commit(new, f"crop {inp.get('aspect')}")
                return {"ok": True, "crop": crop}
            if name == "suggest_mockups":
                ids = [t for t in inp.get("template_ids", []) if mockup_core.get_template(t)]
                self.actions.append({"label": "suggest_mockups", "template_ids": ids, "reason": inp.get("reason", "")})
                return {"ok": True, "valid_template_ids": ids}
            if name == "save_preset":
                s = self.settings.merged(inp["settings"]) if inp.get("settings") else self.settings
                rec = preset_core.save_user_preset(inp["name"], s, inp.get("notes", ""), inp.get("sources") or [])
                self.actions.append({"label": "save_preset", "preset": rec})
                return {"ok": True, "preset_id": rec["id"]}
            return {"ok": False, "error": f"unknown tool {name}"}
        except Exception as e:  # tool errors go back to the model, not to the user as a crash
            log.exception("tool failed")
            return {"ok": False, "error": str(e)}


# --------------------------------------------------------------------------- the service
class AIService:
    def __init__(self, client: Any | None = None, model: str = CLAUDE_MODEL):
        self.model = model
        self._client = client

    @property
    def available(self) -> bool:
        return self._client is not None or anthropic_api_key() is not None

    @property
    def client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=anthropic_api_key())
        return self._client

    def _create(self, **kw):
        return self.client.messages.create(model=self.model, max_tokens=kw.pop("max_tokens", 2500), system=SYSTEM, **kw)

    def _loop(self, session: EditSession, messages: list[dict], tools: list[dict], max_turns: int = 8) -> tuple[str, list[str]]:
        """Standard tool loop. Returns (final text, source URLs seen in web searches)."""
        texts, sources = [], []
        for _ in range(max_turns):
            resp = self._create(messages=messages, tools=tools)
            content = list(resp.content)
            tool_results = []
            for block in content:
                btype = getattr(block, "type", None)
                if btype == "text":
                    texts.append(block.text)
                    for c in (getattr(block, "citations", None) or []):
                        url = getattr(c, "url", None)
                        if url and url not in sources:
                            sources.append(url)
                elif btype == "web_search_tool_result":
                    for r in (getattr(block, "content", None) or []):
                        url = getattr(r, "url", None) if not isinstance(r, dict) else r.get("url")
                        if url and url not in sources:
                            sources.append(url)
                elif btype == "tool_use":
                    inp = dict(block.input or {})
                    if block.name == "save_preset" and sources and not inp.get("sources"):
                        inp["sources"] = sources[:8]
                    result = session.run_tool(block.name, inp)
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result)})
            messages.append({"role": "assistant", "content": _serialise(content)})
            if not tool_results or resp.stop_reason != "tool_use":
                break
            messages.append({"role": "user", "content": tool_results})
        return "\n\n".join(t for t in texts if t.strip()), sources

    # ---- modes
    def critique(self, session: EditSession, user_note: str = "") -> dict:
        msgs = [{"role": "user", "content": session.context_blocks() + [{"type": "text", "text": MODE_PROMPTS["critique"] + _note(user_note)}]}]
        text, _ = self._loop(session, msgs, tools=[], max_turns=1)
        return {"mode": "critique", "text": text, "actions": [], "settings": session.settings.model_dump()}

    def auto_edit(self, session: EditSession, user_note: str = "") -> dict:
        msgs = [{"role": "user", "content": session.context_blocks() + [{"type": "text", "text": MODE_PROMPTS["auto_edit"] + _note(user_note)}]}]
        text, _ = self._loop(session, msgs, tools=TOOLS)
        return {"mode": "auto_edit", "text": text, "actions": session.actions, "settings": session.settings.model_dump()}

    def research(self, session: EditSession, user_note: str = "") -> dict:
        msgs = [{"role": "user", "content": session.context_blocks() + [{"type": "text", "text": MODE_PROMPTS["research"] + _note(user_note)}]}]
        text, sources = self._loop(session, msgs, tools=TOOLS + [WEB_SEARCH_TOOL], max_turns=12)
        preset = next((a["preset"] for a in session.actions if a.get("label") == "save_preset"), None)
        return {"mode": "research", "text": text, "actions": session.actions, "settings": session.settings.model_dump(),
                "sources": sources, "preset": preset}

    def best_of_n(self, session: EditSession, user_note: str = "", n: int = 5, progress=None) -> dict:
        # 1. ask for variants
        prompt = MODE_PROMPTS["best_of_n"].replace("4 to 6", str(n)) + _note(user_note)
        msgs = [{"role": "user", "content": session.context_blocks() + [{"type": "text", "text": prompt}]}]
        resp = self._create(messages=msgs, tools=[PROPOSE_TOOL], tool_choice={"type": "tool", "name": "propose_variants"})
        block = next((b for b in resp.content if getattr(b, "type", None) == "tool_use"), None)
        if block is None:
            raise RuntimeError("model did not propose variants")
        variants = list(block.input.get("variants", []))[:6]
        if progress:
            progress(0.3, f"Rendering {len(variants)} variants")
        # 2. render each (never committed: the user picks)
        rendered = []
        for i, v in enumerate(variants):
            s = EditSettings().merged({"crop": session.settings.crop.model_dump()})
            if v.get("preset_id"):
                try:
                    s = preset_core.apply_preset(s, v["preset_id"], v.get("strength"))
                except KeyError:
                    pass
            s = s.merged(v.get("adjustments") or {})
            img = session.preview(s, long_edge=900)
            rendered.append({"index": i, "name": v.get("name", f"Variant {i + 1}"), "rationale": v.get("rationale", ""),
                             "settings": s.model_dump(), "image": img})
        # 3. side-by-side comparison for Claude
        sheet = _comparison_sheet([r["image"] for r in rendered], [r["name"] for r in rendered])
        msgs.append({"role": "assistant", "content": _serialise(resp.content)})
        msgs.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": block.id, "content": "Variants rendered. The next image compares them side by side."},
            _image_block(_jpeg_b64(sheet, 80)),
            {"type": "text", "text": "Compare the variants (numbered as in the image). Judge natural skin/colour, tonal balance, "
                                     "clipping and overall appeal against Charlie's taste profile. Call rank_variants, best first."}]})
        if progress:
            progress(0.7, "Comparing variants")
        resp2 = self._create(messages=msgs, tools=[PROPOSE_TOOL, RANK_TOOL], tool_choice={"type": "tool", "name": "rank_variants"})
        rb = next((b for b in resp2.content if getattr(b, "type", None) == "tool_use" and b.name == "rank_variants"), None)
        ranking = list(rb.input.get("ranking", [])) if rb else []
        summary = (rb.input.get("summary", "") if rb else "")
        order = [r["index"] for r in ranking if 0 <= int(r.get("index", -1)) < len(rendered)]
        verdicts = {int(r["index"]): r.get("verdict", "") for r in ranking if "index" in r}
        for i in range(len(rendered)):
            if i not in order:
                order.append(i)
        top = []
        for i in order[:3]:
            r = rendered[i]
            top.append({"index": i, "name": r["name"], "rationale": r["rationale"], "verdict": verdicts.get(i, ""),
                        "settings": r["settings"], "image_b64": _jpeg_b64(r["image"], 82)})
        return {"mode": "best_of_n", "text": summary, "top": top,
                "all": [{"index": r["index"], "name": r["name"], "settings": r["settings"]} for r in rendered],
                "settings": session.settings.model_dump(), "actions": []}

    def reel_cut_list(self, sheet_jpeg: bytes, timestamps: list[float], clip_name: str, target_seconds: int = 30,
                      user_note: str = "") -> dict:
        """Ask Claude for a cut list from a contact sheet (1 frame/second)."""
        tool = {"name": "make_cut_list", "description": "Return the segments to keep, in order, for a short reel.",
                "input_schema": {"type": "object", "properties": {
                    "title": {"type": "string"},
                    "segments": {"type": "array", "items": {"type": "object", "properties": {
                        "start": {"type": "number"}, "end": {"type": "number"}, "speed": {"type": "number"},
                        "note": {"type": "string"}}, "required": ["start", "end"]}},
                    "summary": {"type": "string"}}, "required": ["segments", "summary"]}}
        txt = (f"This contact sheet samples clip '{clip_name}' at 1 frame per second; frame #i is at t=i s "
               f"(timestamps: 0..{timestamps[-1]:.0f}s). Build a cut list for a ~{target_seconds}s reel: pick the most "
               f"interesting, varied, well-exposed moments, avoid repeating the same look, keep each segment 1.5-5 s, "
               f"total about {target_seconds}s. Use speed 1.0 unless a slow-motion (0.5) or quick (2.0) moment helps. "
               f"Also suggest a short title. {user_note}")
        msgs = [{"role": "user", "content": [_image_block(base64.b64encode(sheet_jpeg).decode()), {"type": "text", "text": txt}]}]
        resp = self._create(messages=msgs, tools=[tool], tool_choice={"type": "tool", "name": "make_cut_list"})
        block = next((b for b in resp.content if getattr(b, "type", None) == "tool_use"), None)
        if block is None:
            raise RuntimeError("no cut list returned")
        return dict(block.input)


def _note(user_note: str) -> str:
    return f"\n\nCharlie adds: {user_note.strip()}" if user_note and user_note.strip() else ""


def _serialise(content) -> list[dict]:
    """Turn SDK content blocks into plain dicts we can send back as the assistant turn."""
    out = []
    for b in content:
        t = getattr(b, "type", None)
        if t == "text":
            out.append({"type": "text", "text": b.text})
        elif t == "tool_use":
            out.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
        elif t in ("server_tool_use", "web_search_tool_result"):
            # keep server tool blocks verbatim (dict form) so the conversation stays valid
            try:
                out.append(b.model_dump())
            except Exception:
                pass
    return out


def _comparison_sheet(images: list[np.ndarray], names: list[str], cols: int = 3) -> np.ndarray:
    tiles = [Image.fromarray(im) for im in images]
    w = max(t.width for t in tiles)
    h = max(t.height for t in tiles)
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w, rows * (h + 28)), (18, 18, 18))
    d = ImageDraw.Draw(sheet)
    for i, t in enumerate(tiles):
        x, y = (i % cols) * w, (i // cols) * (h + 28)
        sheet.paste(t, (x + (w - t.width) // 2, y + 28))
        d.text((x + 8, y + 7), f"#{i}  {names[i]}", fill=(255, 230, 100))
    return np.array(sheet)
