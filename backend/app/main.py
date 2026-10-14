"""FastAPI app: API routers + the built React UI served as a single-page app.

Run:  uv run python -m backend.app.main [--open] [--port 8765]
"""
from __future__ import annotations

import argparse
import logging
import threading
import warnings
import webbrowser
from pathlib import Path

warnings.filterwarnings("ignore", module="colour")

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .api import ai, google, jobs_api, library, mockups, photos, presets, video
from .core.library import init_db

log = logging.getLogger("ccs")


def ensure_assets() -> None:
    """Built-in filters and mockup templates are committed, but regenerate if missing."""
    from .core.mockups import generate_placeholder_templates, list_templates
    from .core.presets import generate_builtin_filters, list_presets

    if len([p for p in list_presets() if not p["user"]]) < 25:
        log.info("generating built-in filters")
        generate_builtin_filters()
    if len(list_templates()) < 8:
        log.info("generating placeholder mockup templates")
        generate_placeholder_templates()


def create_app() -> FastAPI:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(_app):
        init_db()
        ensure_assets()
        yield

    app = FastAPI(title="Charlie's Camera Studio", version="0.1.0", lifespan=lifespan)
    for r in (library.router, photos.router, presets.router, mockups.router, ai.router, video.router, jobs_api.router,
              google.router):
        app.include_router(r)

    @app.get("/api/health")
    def health():
        from .core.ffmpeg import ffmpeg_exe

        return {"ok": True, "ffmpeg": ffmpeg_exe(), "model": config.CLAUDE_MODEL, "ai": config.anthropic_api_key() is not None}

    @app.exception_handler(Exception)
    async def _err(request, exc):  # surface errors as JSON for the UI
        log.exception("unhandled error")
        return JSONResponse({"detail": str(exc)}, status_code=500)

    dist = config.FRONTEND_DIST
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{full_path:path}")
        def spa(full_path: str):
            f = dist / full_path
            if full_path and f.is_file():
                return FileResponse(f)
            return FileResponse(dist / "index.html")

    return app


app = create_app()


def _already_running(url: str) -> bool:
    """True if another copy of this app already answers on the port."""
    import json
    import urllib.request

    try:
        # never route a localhost check through an http_proxy from the environment
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url + "/api/health", timeout=3) as r:
            return bool(json.load(r).get("ok"))
    except Exception:
        return False


def main() -> None:
    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=config.HOST)
    ap.add_argument("--port", type=int, default=config.PORT)
    ap.add_argument("--open", action="store_true", help="open the browser once the server is up")
    ap.add_argument("--reload", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    url = f"http://{args.host}:{args.port}"
    if _already_running(url):
        print(f"\n  Charlie's Camera Studio is already running -> {url}\n")
        if args.open:
            webbrowser.open(url)
        return
    if args.open:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"\n  Charlie's Camera Studio -> {url}\n")
    uvicorn.run("backend.app.main:app", host=args.host, port=args.port, reload=args.reload, log_level="warning")


if __name__ == "__main__":
    main()
