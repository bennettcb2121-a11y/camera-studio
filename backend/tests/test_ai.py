"""AI modes run against a scripted fake Messages-API client by default (offline,
deterministic). With CCS_LIVE_AI=1 the live tests drive Opus through the Claude login
(Agent SDK runner) exactly as the app does."""
import os

import pytest

from backend.app.core import jobs
from backend.app.core.sidecar import Sidecar
from backend.app.services.ai import AgentRunner, AIService, APIRunner, EditSession, load_preferences
from backend.tests.fake_anthropic import FakeAnthropic


def _svc():
    return AIService(runner=APIRunner(FakeAnthropic()))


def test_status_when_unavailable(app_client):
    from backend.app.api import ai as ai_api

    ai_api.service.reset()
    s = app_client.get("/api/ai/status").json()
    assert s["backend"] == "api" and s["available"] is False and s["model"] == "claude-opus-5-5"
    r = app_client.post("/api/ai/run", json={"path": "x", "mode": "critique"})
    assert r.status_code == 409 and "claude_login" in r.json()["detail"]


def test_critique_makes_no_edits(photos):
    p = photos[0]
    Sidecar(p).reset()
    svc = _svc()
    res = svc.critique(EditSession(p))
    assert "Exposure" in res["text"] and res["actions"] == []
    calls = svc.runner.client.calls
    assert len(calls) == 1 and "tools" not in calls[0]
    first = calls[0]["messages"][0]["content"]
    assert first[0]["type"] == "image" and "histogram_stats" in first[1]["text"]


def test_ask_edits_and_views_result(photos):
    p = photos[3]
    Sidecar(p).reset()
    svc = _svc()
    res = svc.ask(EditSession(p), "make it moodier")
    assert res["settings"]["vignette"] == 25 and res["settings"]["contrast"] == 20
    # the view_photo result went back to Claude as an image block
    second = svc.runner.client.calls[1]["messages"][2]["content"]  # [user, assistant(tool_use), user(tool_results), ...]
    view = next(b for b in second if b["type"] == "tool_result" and isinstance(b["content"], list))
    assert view["content"][0]["type"] == "image"
    with pytest.raises(ValueError):
        svc.ask(EditSession(p), "  ")
    Sidecar(p).reset()


def test_auto_edit_applies_tools_and_is_undoable(photos):
    p = photos[1]
    sc = Sidecar(p)
    sc.reset()
    before_len = len(sc.history_summary()["entries"])
    res = _svc().auto_edit(EditSession(p))
    assert res["settings"]["exposure"] == 0.4 and res["settings"]["crop"]["aspect"] == "4:5"
    assert len(res["actions"]) == 2 and "median" in res["text"]
    sc = Sidecar(p)
    hist = sc.history_summary()
    assert len(hist["entries"]) == before_len + 2
    assert all(e["source"] == "ai" and e["label"].startswith("AI:") for e in hist["entries"][-2:])
    assert sc.undo().crop.aspect == "free"  # AI edits are undoable
    assert sc.undo().exposure == 0.0
    sc.reset()


def test_research_saves_preset_with_sources(photos):
    p = photos[2]
    Sidecar(p).reset()
    svc = _svc()
    res = svc.research(EditSession(p))
    assert res["preset"] is not None
    assert res["preset"]["sources"] and res["preset"]["sources"][0].startswith("https://")
    assert res["settings"]["wb"]["temp"] == 12
    ws = next(t for t in svc.runner.client.calls[0]["tools"] if t.get("type", "").startswith("web_search"))
    assert ws["max_uses"] == 5
    from backend.app.core.presets import delete_user_preset

    delete_user_preset(res["preset"]["id"])
    Sidecar(p).reset()


def test_best_of_n_and_pick(app_client, photos, fake_ai):
    p = str(photos[0])
    app_client.post("/api/photos/reset", json={"path": p})
    r = app_client.post("/api/ai/run", json={"path": p, "mode": "best_of_n", "n": 5})
    assert r.status_code == 200
    j = jobs.queue.wait(r.json()["id"])
    assert j.status == "done", j.error
    res = j.result
    assert len(res["top"]) == 3 and res["top"][0]["name"] == "Warm classic"
    assert all(len(t["image_b64"]) > 1000 for t in res["top"])
    assert len(res["all"]) == 5
    # ranking happened on a side-by-side sheet image
    rank_call = next(c for c in fake_ai.calls if (c.get("tool_choice") or {}).get("name") == "rank_variants")
    assert rank_call["messages"][0]["content"][0]["type"] == "image"
    chosen, rejected = res["top"][0], res["top"][1:]
    r = app_client.post("/api/ai/pick", json={"path": p, "chosen": chosen, "rejected": rejected})
    assert r.status_code == 200
    assert r.json()["settings"]["lut"]["name"] == "kodak-warm"
    assert r.json()["history"]["entries"][-1]["source"] == "ai"
    assert load_preferences()["picks"][-1]["chosen"]["name"] == "Warm classic"
    # the next request includes the taste profile
    r = app_client.post("/api/ai/run", json={"path": p, "mode": "critique"})
    jobs.queue.wait(r.json()["id"])
    assert "Warm classic" in fake_ai.calls[-1]["messages"][0]["content"][2]["text"]
    assert app_client.post("/api/photos/undo", json={"path": p}).json()["settings"]["lut"]["name"] is None


def test_live_progress_and_batch(app_client, photos, fake_ai):
    paths = [str(photos[1]), str(photos[2])]
    for p in paths:
        app_client.post("/api/photos/reset", json={"path": p})
    r = app_client.post("/api/ai/run", json={"path": paths[0], "mode": "auto_edit"})
    j = jobs.queue.wait(r.json()["id"])
    assert j.status == "done" and j.live and j.live["path"] == paths[0] and j.live["seq"] == 2
    r = app_client.post("/api/ai/batch", json={"paths": paths, "mode": "auto_edit"})
    j = jobs.queue.wait(r.json()["id"])
    assert j.status == "done", j.error
    assert [x["ok"] for x in j.result["results"]] == [True, True]
    for p in paths:
        assert app_client.get("/api/photos/info", params={"path": p}).json()["settings"]["exposure"] == 0.4
        app_client.post("/api/photos/reset", json={"path": p})
    assert app_client.post("/api/ai/run", json={"path": paths[0], "mode": "ask", "note": ""}).status_code == 400


# --------------------------------------------------------------------------- live (Opus via Claude login)
live = pytest.mark.skipif(os.environ.get("CCS_LIVE_AI") != "1", reason="set CCS_LIVE_AI=1 to call Opus via your Claude login")


@pytest.fixture(scope="module")
def live_svc():
    r = AgentRunner()
    if not r.available():
        pytest.skip("not signed in to Claude: uv run python -m backend.app.claude_login")
    return AIService(runner=r)


@live
def test_live_critique(photos, live_svc):
    res = live_svc.critique(EditSession(photos[0]))
    assert len(res["text"]) > 50


@live
def test_live_ask_edits_undoably(photos, live_svc):
    p = photos[0]
    Sidecar(p).reset()
    res = live_svc.ask(EditSession(p), "Make this warmer and a bit brighter, keep it natural.")
    s = res["settings"]
    assert s["wb"]["temp"] > 0 or s["exposure"] > 0 or s["lut"]["name"]
    assert any(e["source"] == "ai" for e in Sidecar(p).history_summary()["entries"])
    Sidecar(p).reset()


@live
def test_live_best_of_n(photos, live_svc):
    res = live_svc.best_of_n(EditSession(photos[1]), n=4)
    assert len(res["top"]) == 3 and all(t["settings"] for t in res["top"])


@live
def test_live_research(photos, live_svc):
    p = photos[4] if len(photos) > 4 else photos[-1]
    Sidecar(p).reset()
    res = live_svc.research(EditSession(p))
    assert res["preset"] is not None, res["text"]
    from backend.app.core.presets import delete_user_preset

    delete_user_preset(res["preset"]["id"])
    Sidecar(p).reset()
