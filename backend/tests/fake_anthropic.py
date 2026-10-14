"""A scripted stand-in for anthropic.Anthropic used when no API key is available.

It mimics the parts of the Messages API the AI service touches: `messages.create`
returns objects with `.content` blocks (`.type`, `.text`, `.name`, `.input`, `.id`)
and `.stop_reason`. Behaviour is keyed on the mode prompt and the tools offered, and
it reacts to tool results the way a real model would (ends the turn after seeing them).
"""
from __future__ import annotations

import itertools
import json
from types import SimpleNamespace as NS

_ids = itertools.count(1)


def text(t):
    return NS(type="text", text=t, citations=None)


def tool_use(name, inp):
    return NS(type="tool_use", id=f"toolu_{next(_ids)}", name=name, input=inp)


def web_result(urls):
    return NS(type="web_search_tool_result", tool_use_id=f"srvtoolu_{next(_ids)}",
              content=[NS(url=u, title=u.split("/")[2]) for u in urls],
              model_dump=lambda: {"type": "web_search_tool_result", "tool_use_id": "x", "content": []})


def msg(blocks, stop="end_turn"):
    return NS(content=blocks, stop_reason=stop, model="fake", usage=NS(input_tokens=1, output_tokens=1))


def _tr_text(c):
    """tool_result content is a JSON string, or [image, text] blocks for view_photo."""
    if isinstance(c, list):
        return next((b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"), "")
    return c or ""


class _Messages:
    def __init__(self, parent):
        self.parent = parent

    def create(self, **kw):
        self.parent.calls.append(kw)
        messages = kw["messages"]
        tools = {t.get("name") for t in kw.get("tools", [])}
        last = messages[-1]
        content = last["content"] if isinstance(last["content"], list) else [{"type": "text", "text": last["content"]}]
        user_text = " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
        has_tool_result = any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
        forced = (kw.get("tool_choice") or {}).get("name")

        if forced == "propose_variants":
            return msg([tool_use("propose_variants", {"variants": [
                {"name": "Bright & airy", "rationale": "lift exposure, soften contrast", "adjustments": {"exposure": 0.5, "contrast": -10, "highlights": -20}},
                {"name": "Moody film", "rationale": "darker, teal shadows", "adjustments": {"exposure": -0.3, "contrast": 20, "vibrance": -10}, "preset_id": "tungsten-night", "strength": 0.8},
                {"name": "Punchy", "rationale": "contrast and clarity", "adjustments": {"contrast": 25, "clarity": 30, "vibrance": 20}},
                {"name": "Warm classic", "rationale": "warm WB, gentle fade", "adjustments": {"wb": {"temp": 15}, "exposure": 0.2}, "preset_id": "kodak-warm"},
                {"name": "Clean mono", "rationale": "black and white", "adjustments": {"clarity": 10}, "preset_id": "tri-x-mono"},
            ]})], "tool_use")
        if forced == "rank_variants":
            return msg([tool_use("rank_variants", {"ranking": [{"index": 3, "verdict": "most natural"}, {"index": 0, "verdict": "bright but clean"},
                                                               {"index": 2, "verdict": "a bit strong"}, {"index": 1, "verdict": "too dark"}],
                                                   "summary": "Warm classic keeps the scene believable; Bright & airy is a close second."})], "tool_use")
        if forced == "make_cut_list":
            return msg([tool_use("make_cut_list", {"title": "Test reel", "summary": "three varied moments",
                                                   "segments": [{"start": 0.2, "end": 2.0}, {"start": 2.2, "end": 3.6, "speed": 0.5}, {"start": 4.0, "end": 5.5}]})], "tool_use")

        if has_tool_result:  # second turn of an agentic loop
            results = [json.loads(_tr_text(b["content"])) for b in content if isinstance(b, dict) and b.get("type") == "tool_result"
                       and _tr_text(b["content"]).startswith("{")]
            errs = [r for r in results if not r.get("ok", True)]
            if "web_search" in tools and not self.parent.saved_preset:
                self.parent.saved_preset = True
                return msg([text("Based on what I found, golden-hour portraits are usually edited warm with lifted shadows."),
                            tool_use("save_preset", {"name": "Research: golden hour portrait", "notes": "Warm WB, lifted shadows, soft highlights; sources from the web search.",
                                                     "sources": ["https://example.com/golden-hour-editing", "https://example.org/portrait-retouch"]})], "tool_use")
            if errs:
                return msg([text(f"One tool failed: {errs[0].get('error')}")])
            stats = next((r.get("histogram_stats") for r in results if r.get("histogram_stats")), None)
            s = f" The new median luminance is {stats['median']:.2f}." if stats else ""
            return msg([text("Done. I lifted exposure by +0.4 EV to open the shadows, pulled highlights down to keep the sky, "
                             "and added a touch of vibrance." + s)])

        if "Mode: ASK" in user_text and "set_adjustments" in tools:
            return msg([tool_use("set_adjustments", {"adjustments": {"exposure": -0.3, "contrast": 20, "vignette": 25},
                                                     "reason": "Moodier: darker, more contrast, vignette"}),
                        tool_use("view_photo", {})], "tool_use")
        if "CRITIQUE" in user_text:
            return msg([text("- Exposure: slightly under; the midtones sit around 0.25.\n- Colour: neutral, no cast.\n"
                             "- Composition: busy foreground; a tighter 4:5 crop would help.\n- Sharpness: fine at ISO 100.")])
        if "RESEARCH" in user_text and "web_search" in tools:
            return msg([NS(type="server_tool_use", id="srv1", name="web_search", input={"query": "how to edit golden hour portraits"},
                           model_dump=lambda: {"type": "server_tool_use", "id": "srv1", "name": "web_search", "input": {"query": "q"}}),
                        web_result(["https://example.com/golden-hour-editing", "https://example.org/portrait-retouch"]),
                        text("Photographers warm the white balance and lift shadows for this light.", ),
                        tool_use("set_adjustments", {"adjustments": {"wb": {"temp": 12}, "shadows": 25, "highlights": -15, "vibrance": 10},
                                                     "reason": "Warm, lifted look typical for this light"})], "tool_use")
        if "AUTO-EDIT" in user_text and "set_adjustments" in tools:
            return msg([text("I'll brighten it a little and protect the highlights."),
                        tool_use("set_adjustments", {"adjustments": {"exposure": 0.4, "highlights": -30, "vibrance": 15},
                                                     "reason": "Open the shadows without clipping"}),
                        tool_use("crop", {"aspect": "4:5", "reason": "Tighter framing"})], "tool_use")
        return msg([text("OK.")])


class FakeAnthropic:
    def __init__(self, *a, **k):
        self.calls: list[dict] = []
        self.saved_preset = False
        self.messages = _Messages(self)
