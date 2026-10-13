"""AI assistant: Ask, Critique, Auto-edit, Research and Best-of-N, plus the reel cut list.

Every request carries a ~1500px JPEG of the current edit, EXIF, histogram stats, the
current settings, the filter list and Charlie's taste profile (logged Best-of-N picks).
Claude edits through tools that go through the normal sidecar path, so every AI change
is an undoable history entry (source="ai") and the UI watches the photo change live.

Two interchangeable runners talk to Claude:
  AgentRunner - Opus through Charlie's Claude login, via the Claude Agent SDK and its
                bundled `claude` binary. No API key. Tools are in-process MCP tools;
                Research uses Claude's built-in WebSearch (capped at 5 searches).
  APIRunner   - the Messages API with ANTHROPIC_API_KEY (or any injected client; the
                tests inject a scripted fake).
The modes are written once against the small Runner interface (`agent`, `structured`).
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import re
import time
import warnings
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw

from ..config import AI_BACKEND, AI_EFFORT, AI_PREFERENCES_PATH, AI_PREVIEW_LONG_EDGE, CLAUDE_MODEL, anthropic_api_key
from ..core import mockups as mockup_core
from ..core import presets as preset_core
from ..core.images import proxy_cache, read_exif, resize_long_edge
from ..core.pipeline import crop_rect_for_aspect, finalize, histogram, render
from ..core.settings_schema import ASPECT_PRESETS, SLIDER_RANGES, EditSettings
from ..core.sidecar import Sidecar

log = logging.getLogger(__name__)
# The studio's own tools are pre-approved on purpose; can_use_tool only rations WebSearch.
warnings.filterwarnings("ignore", message=r"can_use_tool will not be invoked")

MODES = ("ask", "critique", "auto_edit", "research", "best_of_n")
MAX_WEB_SEARCHES = 5

# --------------------------------------------------------------------------- tool schemas
ADJ_PROPS: dict = {k: {"type": "number", "minimum": lo, "maximum": hi} for k, (lo, hi) in SLIDER_RANGES.items() if "." not in k}
ADJ_PROPS["wb"] = {"type": "object", "properties": {"temp": {"type": "number", "minimum": -100, "maximum": 100},
                                                    "tint": {"type": "number", "minimum": -100, "maximum": 100}}}
ADJ_PROPS["curve"] = {"type": "array", "description": "Tone curve points [[x,y],...] in 0..1, must include [0,0] and [1,1]",
                      "items": {"type": "array", "items": {"type": "number"}}}
ADJ_PROPS["hsl"] = {"type": "object", "description": "8 bands: red, orange, yellow, green, aqua, blue, purple, magenta",
                    "properties": {k: {"type": "array", "items": {"type": "number"}, "minItems": 8, "maxItems": 8} for k in ("hue", "sat", "lum")}}
ADJ_PROPS["lut"] = {"type": "object", "properties": {"name": {"type": ["string", "null"]}, "strength": {"type": "number", "minimum": 0, "maximum": 1}}}

TOOLS: list[dict] = [
    {"name": "set_adjustments", "description": "Change one or more sliders. Values are absolute (not deltas) and replace the "
     "current value. Omit sliders you do not want to change. Returns the new histogram stats.",
     "input_schema": {"type": "object", "properties": {"adjustments": {"type": "object", "properties": ADJ_PROPS},
                                                       "reason": {"type": "string", "description": "One sentence shown to Charlie"}},
                      "required": ["adjustments", "reason"]}},
    {"name": "apply_filter", "description": "Apply one of the built-in or user filters by id, with optional LUT strength 0..1. "
     "This replaces the filter's sliders, so apply filters before fine-tuning with set_adjustments.",
     "input_schema": {"type": "object", "properties": {"preset_id": {"type": "string"}, "strength": {"type": "number", "minimum": 0, "maximum": 1},
                                                       "reason": {"type": "string"}}, "required": ["preset_id", "reason"]}},
    {"name": "crop", "description": "Crop/straighten. aspect is one of free, original, 1:1, 4:5, 9:16, 3:2, 16:9. x,y,w,h are "
     "fractions of the image (omit for the largest centred crop of that aspect). rotate is degrees.",
     "input_schema": {"type": "object", "properties": {"aspect": {"type": "string", "enum": list(ASPECT_PRESETS)},
                                                       "x": {"type": "number"}, "y": {"type": "number"}, "w": {"type": "number"},
                                                       "h": {"type": "number"}, "rotate": {"type": "number"}, "reason": {"type": "string"}},
                      "required": ["aspect", "reason"]}},
    {"name": "view_photo", "description": "Look at the photo as it is now (after your edits) to check the result. "
     "Use it after a significant change, at most two or three times per request.",
     "input_schema": {"type": "object", "properties": {}}},
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
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_WEB_SEARCHES}

VARIANTS_SCHEMA = {"type": "object", "properties": {"variants": {"type": "array", "minItems": 3, "maxItems": 6, "items": {
    "type": "object", "properties": {"name": {"type": "string"}, "rationale": {"type": "string"},
                                     "adjustments": {"type": "object", "properties": ADJ_PROPS},
                                     "preset_id": {"type": ["string", "null"]}, "strength": {"type": "number"}},
    "required": ["name", "rationale", "adjustments"]}}}, "required": ["variants"]}
RANKING_SCHEMA = {"type": "object", "properties": {"ranking": {"type": "array", "items": {"type": "object", "properties": {
    "index": {"type": "integer"}, "verdict": {"type": "string"}}, "required": ["index", "verdict"]}},
    "summary": {"type": "string"}}, "required": ["ranking", "summary"]}
CUT_LIST_SCHEMA = {"type": "object", "properties": {
    "title": {"type": "string"},
    "segments": {"type": "array", "items": {"type": "object", "properties": {
        "start": {"type": "number"}, "end": {"type": "number"}, "speed": {"type": "number"}, "note": {"type": "string"}},
        "required": ["start", "end"]}},
    "summary": {"type": "string"}}, "required": ["segments", "summary"]}

SYSTEM = """You are the editing assistant inside Charlie's Camera Studio, a personal photo editor for a Sony a6000.
You see a preview JPEG of the current edit, the EXIF, histogram statistics, the current slider values, the list of
available filters and Charlie's taste profile. Sliders: exposure in EV (-5..5); contrast, highlights, shadows,
whites, blacks, vibrance, saturation, clarity, vignette in -100..100; clahe, denoise, sharpen in 0..100;
wb.temp (- cooler, + warmer) and wb.tint (- green, + magenta) in -100..100 relative to the camera white balance;
vignette - darkens the edges, + lightens them (like Lightroom).
Be concrete and brief; speak to Charlie directly. Favour natural, believable results unless asked otherwise.
Only use the tools you are given; never ask Charlie questions, make a sensible choice instead."""

MODE_PROMPTS = {
    "ask": "Mode: ASK. Charlie's request is below. If it asks for changes, make them with the tools (set_adjustments, "
           "apply_filter, crop), check the result with view_photo, refine if needed, then reply in two or three short "
           "lines saying what you changed. If it is a question, just answer it.",
    "critique": "Mode: CRITIQUE. Explain the photo's strengths and the issues you see (exposure, colour, composition, "
                "focus/noise, crop) and what you would change, as a short bulleted list. Do NOT edit anything.",
    "auto_edit": "Mode: AUTO-EDIT. Improve the photo using the tools (set_adjustments, apply_filter, crop). Make the edit, "
                 "check it with view_photo and the returned histogram stats, refine once if needed, then finish with a "
                 "short summary where each change is explained in one line.",
    "research": f"Mode: RESEARCH. First use web search (at most {MAX_WEB_SEARCHES} searches) to learn how photographers "
                "typically edit this genre and lighting situation (subject type, time of day, light quality). Then apply "
                "a fitting edit with set_adjustments/apply_filter and finally call save_preset with a descriptive name, "
                "notes that summarise what you learned, and the source URLs. End with a short summary.",
    "best_of_n": "Mode: BEST-OF-N. Propose 4 to 6 genuinely different edits (different moods, not tiny variations). "
                 "Each must be a complete set of absolute slider values; preset_id optionally starts from a filter.",
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


def _text(t: str) -> dict:
    return {"type": "text", "text": t}


def _settings_delta(prev: EditSettings, new: EditSettings) -> dict:
    a, b = prev.model_dump(), new.model_dump()
    return {k: b[k] for k in b if a[k] != b[k]}


_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")


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
        self.sources: list[str] = []

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
                _text("Photo context (JSON):\n" + json.dumps(info, indent=1)),
                _text(taste_profile_text())]

    # ---- tool execution: returns a JSON-able dict; "_image" carries a picture back to Claude
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
            if name == "view_photo":
                return {"ok": True, "histogram_stats": self.stats(), "_image": _jpeg_b64(self.preview(long_edge=1200), 82)}
            if name == "suggest_mockups":
                ids = [t for t in inp.get("template_ids", []) if mockup_core.get_template(t)]
                self.actions.append({"label": "suggest_mockups", "template_ids": ids, "reason": inp.get("reason", "")})
                return {"ok": True, "valid_template_ids": ids}
            if name == "save_preset":
                s = self.settings.merged(inp["settings"]) if inp.get("settings") else self.settings
                sources = inp.get("sources") or self.sources[:8]
                rec = preset_core.save_user_preset(inp["name"], s, inp.get("notes", ""), sources)
                self.actions.append({"label": "save_preset", "preset": rec})
                return {"ok": True, "preset_id": rec["id"]}
            return {"ok": False, "error": f"unknown tool {name}"}
        except Exception as e:  # tool errors go back to the model, not to the user as a crash
            log.exception("tool failed")
            return {"ok": False, "error": str(e)}


# --------------------------------------------------------------------------- runners
class APIRunner:
    """Messages API (API key, or an injected client such as the test fake)."""

    name = "api"

    def __init__(self, client: Any | None = None, model: str = CLAUDE_MODEL):
        self.model = model
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=anthropic_api_key())
        return self._client

    def available(self) -> bool:
        return self._client is not None or anthropic_api_key() is not None

    def _create(self, **kw):
        return self.client.messages.create(model=self.model, max_tokens=kw.pop("max_tokens", 3000), system=SYSTEM, **kw)

    def agent(self, session: EditSession, blocks: list[dict], *, edit_tools: bool, web_search: bool, max_turns: int) -> str:
        tools = list(TOOLS) if edit_tools else []
        if web_search:
            tools = tools + [WEB_SEARCH_TOOL]
        messages = [{"role": "user", "content": blocks}]
        texts: list[str] = []
        for _ in range(max_turns):
            resp = self._create(messages=messages, tools=tools) if tools else self._create(messages=messages)
            content = list(resp.content)
            tool_results = []
            for block in content:
                btype = getattr(block, "type", None)
                if btype == "text":
                    texts.append(block.text)
                    for c in (getattr(block, "citations", None) or []):
                        url = getattr(c, "url", None)
                        if url and url not in session.sources:
                            session.sources.append(url)
                elif btype == "web_search_tool_result":
                    for r in (getattr(block, "content", None) or []):
                        url = getattr(r, "url", None) if not isinstance(r, dict) else r.get("url")
                        if url and url not in session.sources:
                            session.sources.append(url)
                elif btype == "tool_use":
                    result = session.run_tool(block.name, dict(block.input or {}))
                    img = result.pop("_image", None)
                    payload: Any = json.dumps(result)
                    if img:
                        payload = [_image_block(img), _text(payload)]
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": payload})
            messages.append({"role": "assistant", "content": _serialise(content)})
            if not tool_results or resp.stop_reason != "tool_use":
                break
            messages.append({"role": "user", "content": tool_results})
        return "\n\n".join(t for t in texts if t.strip())

    def structured(self, blocks: list[dict], schema: dict, name: str) -> dict:
        tool = {"name": name, "description": f"Return the {name.replace('_', ' ')} result.", "input_schema": schema}
        resp = self._create(messages=[{"role": "user", "content": blocks}], tools=[tool], tool_choice={"type": "tool", "name": name})
        block = next((b for b in resp.content if getattr(b, "type", None) == "tool_use"), None)
        if block is None:
            raise RuntimeError(f"model did not return {name}")
        return dict(block.input)


class AgentRunner:
    """Opus through Charlie's Claude login (Claude Agent SDK + bundled claude CLI)."""

    name = "claude"

    def __init__(self, model: str = CLAUDE_MODEL, effort: str | None = AI_EFFORT):
        self.model = model
        self.effort = effort

    def available(self) -> bool:
        from ..claude_login import auth_status

        return bool(auth_status().get("loggedIn"))

    def _options(self, **kw):
        from claude_agent_sdk import ClaudeAgentOptions

        from ..claude_login import cli_path

        opts = dict(model=self.model, system_prompt=SYSTEM, setting_sources=[], strict_mcp_config=True,
                    env={"ENABLE_CLAUDEAI_MCP_SERVERS": "false", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"},
                    cli_path=cli_path())
        if self.effort:
            opts["effort"] = self.effort
        opts.update(kw)
        return ClaudeAgentOptions(**opts)

    @staticmethod
    async def _prompt(blocks: list[dict]):
        yield {"type": "user", "message": {"role": "user", "content": blocks}}

    def agent(self, session: EditSession, blocks: list[dict], *, edit_tools: bool, web_search: bool, max_turns: int) -> str:
        from claude_agent_sdk import (AssistantMessage, PermissionResultAllow, PermissionResultDeny, ResultMessage, TextBlock,
                                      ToolResultBlock, UserMessage, create_sdk_mcp_server, query, tool)

        mcp_tools = []
        if edit_tools:
            for spec in TOOLS:
                def make(spec=spec):
                    @tool(spec["name"], spec["description"], spec["input_schema"])
                    async def handler(args):
                        result = await asyncio.to_thread(session.run_tool, spec["name"], dict(args or {}))
                        img = result.pop("_image", None)
                        content = [{"type": "text", "text": json.dumps(result)}]
                        if img:
                            content.insert(0, {"type": "image", "data": img, "mimeType": "image/jpeg"})
                        return {"content": content}
                    return handler
                mcp_tools.append(make())
        allowed = [f"mcp__studio__{t['name']}" for t in TOOLS] if edit_tools else []
        builtin = ["WebSearch"] if web_search else []
        searches = {"n": 0}

        async def can_use_tool(name, inp, ctx):
            if name == "WebSearch" and searches["n"] < MAX_WEB_SEARCHES:
                searches["n"] += 1
                return PermissionResultAllow()
            return PermissionResultDeny(message=f"{name} is not available here (web search limit is {MAX_WEB_SEARCHES}).")

        kw: dict = dict(tools=builtin, allowed_tools=allowed, max_turns=max_turns, can_use_tool=can_use_tool)
        if mcp_tools:
            kw["mcp_servers"] = {"studio": create_sdk_mcp_server("studio", tools=mcp_tools)}
        options = self._options(**kw)

        async def run() -> str:
            texts: list[str] = []
            final = ""
            async for m in query(prompt=self._prompt(blocks), options=options):
                if isinstance(m, AssistantMessage):
                    texts.extend(b.text for b in m.content if isinstance(b, TextBlock))
                elif isinstance(m, UserMessage) and isinstance(m.content, list):
                    for b in m.content:  # web search results arrive as tool results: harvest source URLs
                        if isinstance(b, ToolResultBlock) and b.content:
                            raw = b.content if isinstance(b.content, str) else json.dumps(b.content)
                            for url in _URL_RE.findall(raw):
                                if url not in session.sources and "anthropic.com" not in url:
                                    session.sources.append(url)
                elif isinstance(m, ResultMessage):
                    if m.is_error:
                        raise RuntimeError(f"Claude error: {m.result or m.subtype}")
                    final = m.result or ""
            # the final message is what Charlie should read; earlier texts are thinking-aloud between tools
            return final or (texts[-1] if texts else "")

        return asyncio.run(run())

    def structured(self, blocks: list[dict], schema: dict, name: str) -> dict:
        from claude_agent_sdk import ResultMessage, query

        options = self._options(tools=[], max_turns=3, output_format={"type": "json_schema", "schema": schema})

        async def run() -> dict:
            out = None
            async for m in query(prompt=self._prompt(blocks), options=options):
                if isinstance(m, ResultMessage):
                    if m.is_error:
                        raise RuntimeError(f"Claude error: {m.result or m.subtype}")
                    out = m.structured_output
                    if out is None and m.result:
                        try:
                            out = json.loads(m.result)
                        except ValueError:
                            pass
            if not isinstance(out, dict):
                raise RuntimeError(f"Claude did not return {name}")
            return out

        return asyncio.run(run())


def default_runner():
    """Pick the runner from config: Claude login first (no key needed), else API key."""
    if AI_BACKEND == "api":
        return APIRunner()
    agent = AgentRunner()
    if AI_BACKEND == "claude":
        return agent
    try:
        if agent.available():
            return agent
    except Exception:
        pass
    return APIRunner() if anthropic_api_key() else agent


# --------------------------------------------------------------------------- the service
class AIService:
    def __init__(self, client: Any | None = None, runner: Any | None = None):
        self._runner = runner or (APIRunner(client) if client is not None else None)

    @property
    def runner(self):
        if self._runner is None:
            self._runner = default_runner()
        return self._runner

    @runner.setter
    def runner(self, r):
        self._runner = r

    @property
    def available(self) -> bool:
        try:
            return self.runner.available()
        except Exception:
            return False

    def reset(self) -> None:
        """Re-pick the runner (e.g. after signing in)."""
        self._runner = None

    def _run_agent(self, session: EditSession, mode: str, note: str, edit_tools: bool, web_search: bool = False,
                   max_turns: int = 12) -> str:
        prompt = MODE_PROMPTS[mode] + _note(note, mode)
        return self.runner.agent(session, session.context_blocks() + [_text(prompt)], edit_tools=edit_tools,
                                 web_search=web_search, max_turns=max_turns)

    def _result(self, mode: str, session: EditSession, text: str, **extra) -> dict:
        return {"mode": mode, "text": text, "actions": session.actions, "settings": session.settings.model_dump(), **extra}

    # ---- modes
    def ask(self, session: EditSession, note: str) -> dict:
        if not note.strip():
            raise ValueError("Type what you'd like Claude to do")
        return self._result("ask", session, self._run_agent(session, "ask", note, edit_tools=True))

    def critique(self, session: EditSession, note: str = "") -> dict:
        text = self._run_agent(session, "critique", note, edit_tools=False, max_turns=2)
        return {"mode": "critique", "text": text, "actions": [], "settings": session.settings.model_dump()}

    def auto_edit(self, session: EditSession, note: str = "") -> dict:
        return self._result("auto_edit", session, self._run_agent(session, "auto_edit", note, edit_tools=True))

    def research(self, session: EditSession, note: str = "") -> dict:
        text = self._run_agent(session, "research", note, edit_tools=True, web_search=True, max_turns=20)
        preset = next((a["preset"] for a in session.actions if a.get("label") == "save_preset"), None)
        return self._result("research", session, text, sources=session.sources, preset=preset)

    def best_of_n(self, session: EditSession, note: str = "", n: int = 5, progress=None) -> dict:
        prompt = MODE_PROMPTS["best_of_n"].replace("4 to 6", str(n)) + _note(note, "best_of_n")
        proposed = self.runner.structured(session.context_blocks() + [_text(prompt)], VARIANTS_SCHEMA, "propose_variants")
        variants = list(proposed.get("variants", []))[:6]
        if not variants:
            raise RuntimeError("Claude did not propose any variants")
        if progress:
            progress(0.3, f"Rendering {len(variants)} variants")
        rendered = []
        for i, v in enumerate(variants):
            s = EditSettings().merged({"crop": session.settings.crop.model_dump()})
            if v.get("preset_id"):
                try:
                    s = preset_core.apply_preset(s, v["preset_id"], v.get("strength"))
                except KeyError:
                    pass
            s = s.merged(v.get("adjustments") or {})
            rendered.append({"index": i, "name": v.get("name", f"Variant {i + 1}"), "rationale": v.get("rationale", ""),
                             "settings": s.model_dump(), "image": session.preview(s, long_edge=900)})
        if progress:
            progress(0.6, "Comparing variants side by side")
        sheet = _comparison_sheet([r["image"] for r in rendered], [r["name"] for r in rendered])
        listing = "\n".join(f"#{r['index']} {r['name']}: {r['rationale']}" for r in rendered)
        ranked = self.runner.structured(
            [_image_block(_jpeg_b64(sheet, 80)),
             _text(f"These are {len(rendered)} candidate edits of the same photo, numbered as in the image:\n{listing}\n\n"
                   f"{taste_profile_text()}\n\nJudge natural skin/colour, tonal balance, clipping and overall appeal against "
                   "Charlie's taste. Rank them best first with a one-line verdict each, plus a one-sentence summary.")],
            RANKING_SCHEMA, "rank_variants")
        ranking = list(ranked.get("ranking", []))
        order = [int(r["index"]) for r in ranking if 0 <= int(r.get("index", -1)) < len(rendered)]
        verdicts = {int(r["index"]): r.get("verdict", "") for r in ranking if "index" in r}
        order = list(dict.fromkeys(order + list(range(len(rendered)))))
        top = [{"index": i, "name": rendered[i]["name"], "rationale": rendered[i]["rationale"], "verdict": verdicts.get(i, ""),
                "settings": rendered[i]["settings"], "image_b64": _jpeg_b64(rendered[i]["image"], 82)} for i in order[:3]]
        return {"mode": "best_of_n", "text": ranked.get("summary", ""), "top": top,
                "all": [{"index": r["index"], "name": r["name"], "settings": r["settings"]} for r in rendered],
                "settings": session.settings.model_dump(), "actions": []}

    def run_mode(self, mode: str, session: EditSession, note: str = "", n: int = 5, progress=None) -> dict:
        if mode == "ask":
            return self.ask(session, note)
        if mode == "critique":
            return self.critique(session, note)
        if mode == "auto_edit":
            return self.auto_edit(session, note)
        if mode == "research":
            return self.research(session, note)
        if mode == "best_of_n":
            return self.best_of_n(session, note, n=n, progress=progress)
        raise ValueError(f"unknown mode {mode}")

    def reel_cut_list(self, sheet_jpeg: bytes, timestamps: list[float], clip_name: str, target_seconds: int = 30,
                      user_note: str = "") -> dict:
        """Ask Claude for a cut list from a contact sheet (1 frame/second)."""
        txt = (f"This contact sheet samples clip '{clip_name}' at 1 frame per second; frame #i is at t=i s "
               f"(timestamps: 0..{timestamps[-1]:.0f}s). Build a cut list for a ~{target_seconds}s reel: pick the most "
               f"interesting, varied, well-exposed moments, avoid repeating the same look, keep each segment 1.5-5 s, "
               f"total about {target_seconds}s. Use speed 1.0 unless a slow-motion (0.5) or quick (2.0) moment helps. "
               f"Also suggest a short title. {user_note}")
        return self.runner.structured([_image_block(base64.b64encode(sheet_jpeg).decode()), _text(txt)], CUT_LIST_SCHEMA,
                                      "make_cut_list")


def _note(user_note: str, mode: str) -> str:
    if not user_note or not user_note.strip():
        return ""
    label = "Charlie's request" if mode == "ask" else "Charlie adds"
    return f"\n\n{label}: {user_note.strip()}"


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
