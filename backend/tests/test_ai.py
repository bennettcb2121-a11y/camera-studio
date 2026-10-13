"""AI modes run against the scripted fake client (no API key in this environment).
Set ANTHROPIC_API_KEY and run `uv run pytest -k live` to exercise the real model."""
import os

import pytest

from backend.app.core import jobs
from backend.app.core.sidecar import Sidecar
from backend.app.services.ai import AIService, EditSession, load_preferences
from backend.tests.fake_anthropic import FakeAnthropic


def test_status_without_key(app_client):
    s = app_client.get("/api/ai/status").json()
    assert s["key_set"] is False
    r = app_client.post("/api/ai/run", json={"path": "x", "mode": "critique"})
    assert r.status_code == 409 and "ANTHROPIC_API_KEY" in r.json()["detail"]


def test_critique_makes_no_edits(photos):
    p = photos[0]
    Sidecar(p).reset()
    svc = AIService(client=FakeAnthropic())
    session = EditSession(p)
    res = svc.critique(session)
    assert "Exposure" in res["text"] and res["actions"] == []
    assert len(svc._client.calls) == 1
    first = svc._client.calls[0]["messages"][0]["content"]
    assert first[0]["type"] == "image" and "histogram_stats" in first[1]["text"]


def test_auto_edit_applies_tools_and_is_undoable(photos):
    p = photos[1]
    sc = Sidecar(p)
    sc.reset()
    before_len = len(sc.history_summary()["entries"])
    svc = AIService(client=FakeAnthropic())
    res = svc.auto_edit(EditSession(p))
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
    svc = AIService(client=FakeAnthropic())
    res = svc.research(EditSession(p))
    assert res["preset"] is not None
    assert res["preset"]["sources"] and res["preset"]["sources"][0].startswith("https://")
    assert res["settings"]["wb"]["temp"] == 12
    tools = svc._client.calls[0]["tools"]
    ws = next(t for t in tools if t.get("type", "").startswith("web_search"))
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
    # picking commits the chosen edit (undoable) and logs the preference
    chosen, rejected = res["top"][0], res["top"][1:]
    r = app_client.post("/api/ai/pick", json={"path": p, "chosen": chosen, "rejected": rejected})
    assert r.status_code == 200
    assert r.json()["settings"]["lut"]["name"] == "kodak-warm"
    assert r.json()["history"]["entries"][-1]["source"] == "ai"
    prefs = load_preferences()
    assert prefs["picks"][-1]["chosen"]["name"] == "Warm classic"
    # the next request includes the taste profile
    r = app_client.post("/api/ai/run", json={"path": p, "mode": "critique"})
    jobs.queue.wait(r.json()["id"])
    last_call = fake_ai.calls[-1]
    assert "Warm classic" in last_call["messages"][0]["content"][2]["text"]
    assert app_client.post("/api/photos/undo", json={"path": p}).json()["settings"]["lut"]["name"] is None


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="needs API key: live Claude check")
def test_live_critique(photos):
    svc = AIService()
    res = svc.critique(EditSession(photos[0]))
    assert len(res["text"]) > 50
