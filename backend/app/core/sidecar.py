"""JSON sidecar per original: current settings, undo/redo history, rating, favourites.

The sidecar sits next to the original as `<name>.<ext>.ccs.json`. Originals are
never touched. History entries are full settings snapshots (cheap, tiny JSON).
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .settings_schema import EditSettings

MAX_HISTORY = 200
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def sidecar_path(original: Path) -> Path:
    return original.with_name(original.name + ".ccs.json")


def _lock(p: Path) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(str(p), threading.Lock())


class Sidecar:
    def __init__(self, original: Path):
        self.original = Path(original)
        self.path = sidecar_path(self.original)
        self.data: dict[str, Any] = {
            "version": 1,
            "settings": EditSettings().model_dump(),
            "history": [],  # list of {"label", "settings", "ts", "source"}
            "history_index": -1,
            "rating": 0,
            "mockup_favorites": [],
        }
        self.load()

    def load(self) -> "Sidecar":
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text()))
            except Exception:
                pass
        return self

    def save(self) -> None:
        with _lock(self.path):
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=1))
            tmp.replace(self.path)

    # ---- settings + history
    @property
    def settings(self) -> EditSettings:
        return EditSettings.model_validate(self.data.get("settings") or {})

    def set_settings(self, settings: EditSettings, label: str = "Edit", source: str = "user", record: bool = True) -> None:
        self.data["settings"] = settings.model_dump()
        if record:
            hist = self.data["history"][: self.data["history_index"] + 1]
            hist.append({"label": label, "settings": settings.model_dump(), "ts": time.time(), "source": source})
            hist = hist[-MAX_HISTORY:]
            self.data["history"] = hist
            self.data["history_index"] = len(hist) - 1
        self.save()

    def _history_apply(self, idx: int) -> EditSettings:
        hist = self.data["history"]
        if not hist:
            return self.settings
        idx = max(-1, min(idx, len(hist) - 1))
        self.data["history_index"] = idx
        s = EditSettings() if idx < 0 else EditSettings.model_validate(hist[idx]["settings"])
        self.data["settings"] = s.model_dump()
        self.save()
        return s

    def undo(self) -> EditSettings:
        return self._history_apply(self.data["history_index"] - 1)

    def redo(self) -> EditSettings:
        return self._history_apply(self.data["history_index"] + 1)

    def jump(self, idx: int) -> EditSettings:
        return self._history_apply(idx)

    def history_summary(self) -> dict:
        return {
            "index": self.data["history_index"],
            "entries": [{"label": h["label"], "ts": h["ts"], "source": h.get("source", "user")} for h in self.data["history"]],
        }

    def reset(self) -> EditSettings:
        s = EditSettings()
        self.set_settings(s, label="Reset", record=True)
        return s

    # ---- misc
    @property
    def rating(self) -> int:
        return int(self.data.get("rating", 0))

    @rating.setter
    def rating(self, v: int) -> None:
        self.data["rating"] = max(0, min(5, int(v)))
        self.save()
