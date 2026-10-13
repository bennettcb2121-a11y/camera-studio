# Setup for Charlie

Everything below assumes a laptop with **nothing** installed. Steps marked 🟢 are
required; everything else is optional.

## 1. 🟢 Install uv (the only thing you need)

**Mac** – open Terminal and paste:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows** – open PowerShell and paste:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Close the terminal and open a new one afterwards so `uv` is on your PATH.
That's it: uv downloads Python 3.12, FFmpeg and every library the first time you start
the app. You do **not** need Python, Node, Homebrew, FFmpeg or Xcode.

## 2. 🟢 Get the app folder

**Mac** – you already have it: it is this folder (`~/Charlie's Camera Studio`).
On a new machine, after the repository is on GitHub (see section 5):

```bash
git clone git@github.com:YOUR-USER/YOUR-REPO.git "Charlie's Camera Studio"
cd "Charlie's Camera Studio"
```

(`git` is preinstalled on Mac; on Windows install it from https://git-scm.com or
download the repository as a ZIP from GitHub and unzip it.)

## 3. Your Claude API key (only for the AI panel)

1. Create a key at https://console.anthropic.com/ (API keys).
2. In the app folder, copy `.env.example` to `.env`
   (Mac: `cp .env.example .env`; Windows: `copy .env.example .env`).
3. Open `.env` in any text editor and paste the key after `ANTHROPIC_API_KEY=`.

`.env` is ignored by git, so the key never gets committed. Without a key every
other feature works; the AI tab just shows these instructions.

## 4. 🟢 Start the app

**Mac:** double-click `run.sh`, or in Terminal:

```bash
./run.sh
```

**Windows:** double-click `run.bat`.

The first start takes a minute or two (downloads Python + libraries, ~600 MB). It then
opens http://127.0.0.1:8765 in your browser. Keep the terminal window open while you
use the app; close it (or press Ctrl+C) to stop. Press `?` in the app for the
shortcut list; the full feature tour is in [README.md](README.md).

First thing to do in the app: **Library → + Add folder** and pick the folder with your
a6000 photos/videos (the SD card folder `DCIM/100MSDCF` works, or a copy on disk).

## 5. Getting this onto GitHub + the pull request

The build session had no GitHub access (no `gh`, no token), so the branch is committed
locally only. To publish it:

1. On github.com create an empty repository (private is fine), e.g. `camera-studio`.
2. In the app folder run:

```bash
./open_pr.sh git@github.com:YOUR-USER/camera-studio.git
```

That pushes `main` and `build/v1` and opens the "create pull request" page. Paste
[PR_BODY.md](PR_BODY.md) as the description.

## 6. What was checked here, and what you should re-check

| Check | Status in the build session | How you run it |
|---|---|---|
| 1. `./run.sh` from a fresh clone with only uv | ✅ Done (cloned into a temp folder, started, health-checked) | `./run.sh` on your other laptop |
| 2. Raw opens, sliders fast, export = preview (<2%) | ✅ With one real ILCE-6000 ARW from raw.pixls.us | `uv run pytest -q -k "preview_under_150ms or export_matches"` |
| 3. 25 filters + Auto on every test image | ✅ 1 real ARW + 4 synthetic JPGs | `uv run pytest -q -k filters_and_auto` |
| 4. 8 mockups render + arrow keys cycle | ✅ Rendered for every image; arrows checked in the browser | open Mockups, press `←` `→` |
| 5. AI Critique / Auto-edit / Research / Best-of-N | ⚠️ **Mocked** – no API key here. All four modes ran against a scripted fake client; AI edits verified in the undo history | add your key, then `uv run pytest -q -k live` and try the AI tab |
| 6. Trim + LUT + title + 9:16 export | ✅ Synthetic clips; `.MTS` container covered | `uv run pytest -q -k export_9x16`, or in the Video tab |
| 7. All tests pass | ✅ 51 passed, 1 skipped (the live-API test) | `uv run pytest -q` |

**Needs real samples** – things that would benefit from your own files:

- Only one a6000 `.ARW` exists publicly; your own raws (high ISO, backlit, portraits)
  will show whether the Auto exposure/denoise defaults feel right.
- The test videos are synthetic. Real `.MTS` files from the camera should play after
  the proxy is built (first add takes a few seconds per clip); please check audio sync
  and that "Split into shots" finds your real cuts (threshold 27 in
  `backend/app/core/video.py`).
- Lens correction works when lensfun knows the lens: the kit 18-55 OSS, 55-210, 16-50 PZ
  and most Sony/Sigma E-mount primes are in its database.

**Needs API key** – the AI tab (all four modes), "AI: make a 30 s reel", and the taste
profile logging. Research mode uses the web search tool and costs a few cents per run.

## 7. Known issues

- The first open of a raw takes ~1 s (decoding + proxy cache); afterwards it is instant.
- Preview JPEGs are sRGB; there is no display-profile colour management in the browser.
- The video preview plays the **selected clip** only (from its 720p proxy). Use
  **Render draft** to watch the whole timeline with looks and titles.
- Speed changes on the preview player are approximate (browser playback rate);
  exports are exact.
- Windows was not tested here (no Windows machine). The code has no Mac-only paths
  except the native folder/file dialogs, which have PowerShell equivalents, and
  `run.bat` mirrors `run.sh`. If anything fails, run `uv run python -m backend.app.main`
  in a terminal and send me the error.
- Very large libraries (10k+ files) will make the first scan slow; thumbnails are
  built lazily.

## 8. Three things I'd improve next

1. **Masks**: a brush/gradient/subject mask so exposure and clarity can be local
   (the pipeline already works on per-pixel gain maps, so this is mostly UI).
2. **Timeline playback**: play the whole sequence in-browser without a draft render
   (chain the proxies with a hidden second `<video>` for gapless switching).
3. **Real-camera presets**: a Sony "Creative Style" starting point per picture profile
   and DCP-style camera colour matrices, so slider zero looks exactly like the camera JPEG.
