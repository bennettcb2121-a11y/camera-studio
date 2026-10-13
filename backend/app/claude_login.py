"""Claude sign-in for the AI features (no API key needed).

The claude-agent-sdk package ships its own `claude` binary; the AI panel talks to
Opus through it using Charlie's Claude account login. This module finds that binary,
reports whether it is signed in, and runs the one-time interactive sign-in.

    uv run python -m backend.app.claude_login           # sign in now
    uv run python -m backend.app.claude_login --ensure  # ask only if not signed in (used by run.sh)
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

_cache: dict = {"t": 0.0, "status": None}


def cli_path() -> str | None:
    try:
        import claude_agent_sdk

        name = "claude.exe" if sys.platform == "win32" else "claude"
        bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / name
        if bundled.exists():
            return str(bundled)
    except Exception:
        pass
    return shutil.which("claude")


def auth_status(max_age: float = 60.0) -> dict:
    """{"loggedIn": bool, ...} from `claude auth status`, cached for a minute."""
    if _cache["status"] is not None and time.time() - _cache["t"] < max_age:
        return _cache["status"]
    cli = cli_path()
    status: dict = {"loggedIn": False, "cli": cli}
    if cli:
        try:
            r = subprocess.run([cli, "auth", "status"], capture_output=True, text=True, timeout=20)
            status.update(json.loads(r.stdout or "{}"))
        except Exception as e:  # unreadable status counts as signed out
            status["error"] = str(e)
    _cache.update(t=time.time(), status=status)
    return status


def login() -> int:
    cli = cli_path()
    if not cli:
        print("Could not find the Claude sign-in tool. Run `uv sync` and try again.")
        return 1
    _cache["status"] = None
    return subprocess.call([cli, "auth", "login"])


def main() -> int:
    if "--ensure" in sys.argv:
        if auth_status(0).get("loggedIn"):
            return 0
        if not sys.stdin.isatty():
            return 0
        ans = input("\nThe AI assistant uses your Claude account (no API key needed).\n"
                    "Sign in to Claude now? You only do this once. [Y/n] ").strip().lower()
        if ans in ("", "y", "yes"):
            return login()
        print("Skipped. You can sign in later with:  uv run python -m backend.app.claude_login\n")
        return 0
    return login()


if __name__ == "__main__":
    sys.exit(main())
