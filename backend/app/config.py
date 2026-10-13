"""Central configuration: paths, model name, environment."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

# Claude model used by the AI assistant panel.
CLAUDE_MODEL = os.environ.get("CCS_CLAUDE_MODEL", "claude-sonnet-5-5")

DATA_DIR = Path(os.environ.get("CCS_DATA_DIR", ROOT / ".cache"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
PROXY_DIR = DATA_DIR / "proxies"
THUMB_DIR = DATA_DIR / "thumbs"
VIDEO_PROXY_DIR = DATA_DIR / "video_proxies"
EXPORT_DIR = Path(os.environ.get("CCS_EXPORT_DIR", ROOT / "exports"))
DB_PATH = DATA_DIR / "library.db"
for _d in (PROXY_DIR, THUMB_DIR, VIDEO_PROXY_DIR, EXPORT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

PRESETS_DIR = ROOT / "presets"
LUTS_DIR = ROOT / "luts"
MOCKUPS_DIR = ROOT / "mockups"
USER_PRESETS_DIR = DATA_DIR / "user_presets"
USER_PRESETS_DIR.mkdir(parents=True, exist_ok=True)
AI_PREFERENCES_PATH = DATA_DIR / "ai_preferences.json"
FRONTEND_DIST = ROOT / "frontend" / "dist"

PROXY_LONG_EDGE = 2048
THUMB_LONG_EDGE = 320
AI_PREVIEW_LONG_EDGE = 1500

PHOTO_EXTS = {".arw", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}
RAW_EXTS = {".arw"}
VIDEO_EXTS = {".mts", ".mp4", ".mov", ".m2ts"}

HOST = os.environ.get("CCS_HOST", "127.0.0.1")
PORT = int(os.environ.get("CCS_PORT", "8765"))


def anthropic_api_key() -> str | None:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return key or None
