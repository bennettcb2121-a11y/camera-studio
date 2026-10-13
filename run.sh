#!/usr/bin/env bash
# Charlie's Camera Studio — Mac/Linux start script.
# Needs only `uv` (https://docs.astral.sh/uv/). Everything else installs itself.
set -e
cd "$(dirname "$0")"
if ! command -v uv >/dev/null 2>&1; then
  echo "uv is not installed. Install it with:"
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo "then open a new terminal and run ./run.sh again."
  exit 1
fi
echo "Installing / checking Python and dependencies (first run takes a minute)..."
uv sync --frozen --quiet
exec uv run python -m backend.app.main --open "$@"
