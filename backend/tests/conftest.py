"""Test setup: isolated cache dir, a private copy of ./samples (sidecars are written
next to originals, and we never want to touch the real ones), fixtures generated on demand."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

_TMP = Path(tempfile.mkdtemp(prefix="ccs_test_"))
os.environ["CCS_DATA_DIR"] = str(_TMP / "cache")
os.environ["CCS_EXPORT_DIR"] = str(_TMP / "exports")
os.environ["CCS_GOOGLE_IMPORT_DIR"] = str(_TMP / "google_import")
os.environ.pop("ANTHROPIC_API_KEY", None)
# Offline + deterministic by default: the API runner with no key = "AI unavailable".
# Live tests build an AgentRunner explicitly (CCS_LIVE_AI=1).
os.environ.setdefault("CCS_AI_BACKEND", "api")
warnings.filterwarnings("ignore")

from backend.tests import make_fixtures  # noqa: E402

make_fixtures.main()

SAMPLES_SRC = ROOT / "samples"
SAMPLES = _TMP / "samples"
shutil.copytree(SAMPLES_SRC, SAMPLES, ignore=shutil.ignore_patterns("*.ccs.json", ".*"))

PHOTOS = sorted(p for p in SAMPLES.iterdir() if p.suffix.lower() in (".arw", ".jpg", ".jpeg", ".png"))
RAWS = [p for p in PHOTOS if p.suffix.lower() == ".arw"]
VIDEOS = sorted(p for p in SAMPLES.iterdir() if p.suffix.lower() in (".mp4", ".mts"))
MUSIC = next(iter(SAMPLES.glob("*.m4a")), None)


@pytest.fixture(scope="session")
def samples_dir() -> Path:
    return SAMPLES


@pytest.fixture(scope="session")
def photos() -> list[Path]:
    return PHOTOS


@pytest.fixture(scope="session")
def raw_photo() -> Path:
    if not RAWS:
        pytest.skip("needs real samples: no .ARW in ./samples")
    return RAWS[0]


@pytest.fixture(scope="session")
def videos() -> list[Path]:
    return VIDEOS


@pytest.fixture(scope="session")
def music() -> Path:
    return MUSIC


@pytest.fixture(scope="session")
def test_output() -> Path:
    d = ROOT / "test_output"
    d.mkdir(exist_ok=True)
    return d


@pytest.fixture(scope="session")
def app_client():
    from fastapi.testclient import TestClient

    from backend.app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def fake_ai():
    """Swap the API's AI service client for the scripted fake."""
    from backend.app.api import ai as ai_api
    from backend.tests.fake_anthropic import FakeAnthropic

    from backend.app.services.ai import APIRunner

    fake = FakeAnthropic()
    ai_api.service.runner = APIRunner(fake)
    yield fake
    ai_api.service.reset()
