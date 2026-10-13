# Charlie's Camera Studio

A personal photo + video editor for the Sony a6000. Runs locally on a Mac or Windows
laptop, one user, no accounts, no cloud (the only network use is the optional Claude API).

- **Inputs:** `.ARW` raw (24 MP), `.JPG`, video `.MTS` (AVCHD) and `.MP4` up to 1080p.
- **Originals are never modified.** Every edit lives in a JSON sidecar
  (`photo.ARW.ccs.json`) next to the file, with full undo history.
- **Start it:** `./run.sh` (Mac) or `run.bat` (Windows). Only [uv](https://docs.astral.sh/uv/)
  needs to be installed; Python, FFmpeg and every library install themselves on first run.
  The React UI is pre-built and committed, so Node.js is only needed to change the UI.

Plain-English setup for a fresh laptop: **[SETUP_FOR_CHARLIE.md](SETUP_FOR_CHARLIE.md)**.
Why things are the way they are: **[DECISIONS.md](DECISIONS.md)**. What the build
machine had: **[ENVIRONMENT.md](ENVIRONMENT.md)**.

---

## Features

### Library
- Add any folder (native folder dialog on Mac/Windows, or the in-app browser). Subfolders
  are scanned; the index is SQLite in `.cache/library.db`.
- Thumbnails (raw files use the embedded camera JPEG), star ratings, filter by
  photo/video and minimum rating, sort by name/date/rating, multi-select
  (Shift / Cmd-click).

### Photo editor
- One edit pipeline (`backend/app/core/pipeline.py`) used for previews, exports, mockups,
  AI previews and video looks. Order: white balance → exposure → highlights/shadows →
  whites/blacks → tone curve → HSL/vibrance/saturation → LUT (with strength) → local
  contrast (clarity + CLAHE) → denoise → sharpen → crop/rotate → vignette.
  Float32 linear RGB internally, sRGB only at output.
- Previews render from a cached 2048 px linear proxy; a slider change renders in
  roughly 30–110 ms on an M-series Mac (shown bottom-right of the image). Full resolution
  is only used on export.
- Sliders: temp/tint, exposure, contrast, highlights, shadows, whites, blacks, 8-band HSL
  (hue/sat/lum), vibrance, saturation, clarity, CLAHE, denoise, sharpen, vignette, lens
  correction (lensfun, when the lens is in its database), draggable tone curve.
- Histogram with clipping readout, **before/after on `\`** (hold), undo/redo with a
  clickable history list, copy/paste settings across many photos, crop tool with aspect
  presets (free, original, 1:1, 4:5, 9:16, 3:2, 16:9) and straighten.
- Export JPEG / PNG / 16-bit TIFF, optional long-edge resize, into `exports/`. Exports
  match the preview (tested: mean pixel difference under 2%).
- **Auto**: gray-world white balance blended with the camera WB (damped on scenes with
  one dominant colour), histogram-based exposure without clipping, CLAHE local contrast,
  ISO-based denoise, lens correction.

### Filters
- 25 original filters in five groups, each a programmatically generated `.cube` LUT in
  `luts/` plus slider values in `presets/*.json`:
  - **Film:** Kodak Warm, Pastel Portra, Vivid Ektar, Tri-X Mono, Soft Mono, Tungsten Night
  - **Travel:** Golden Hour, Teal & Orange, Tropic, Nordic Cool, Desert Haze
  - **Portrait:** Soft Skin, Matte Fade, Warm Glow, Classic Mono Portrait, Clean Studio
  - **Sport:** Punch, Gritty Mono, Stadium Pop, Crisp Action
  - **Utility:** Neutral Pop, Flat Profile, Night Cleanup, Web Sharp, Sepia
- Live thumbnails of every filter on the current photo in the strip under the image;
  `←`/`→` cycle; strength slider; "Save as preset" stores your own (also used by the AI).
- Drop any `.cube` file into `luts/` and it becomes available to presets.

### Mockup cycler
- A template is a folder in `mockups/` with `background.jpg`, `template.json` (one or
  more 4-corner slots + blend options) and an optional `overlay.png` that is
  multiply-blended on top (shadows, screen glare, paper texture).
- The current edited photo is perspective-warped into the slot(s); multi-slot templates
  (gallery wall, 3×3 grid) are filled with the other photos in the library.
- `←`/`→` next mockup, `↑`/`↓` next photo, `F` favourite, **Export all as ZIP**.
- 8 placeholder templates are generated procedurally: framed print on a wall, gallery
  wall of three, phone, laptop, Instagram post, Instagram 3×3 grid, poster, magazine
  spread. **New template…** lets you drag four corners onto any image (upload or a
  library photo) and save it.

### AI assistant (Claude)
Each request sends a ~1500 px JPEG of the current edit, EXIF (ISO, shutter, aperture,
lens, focal length), histogram stats, the current settings, the filter list and your
taste profile. Claude has the tools `set_adjustments`, `apply_filter`, `crop`,
`suggest_mockups` and `save_preset`; tool calls are applied through the normal edit path,
so you watch the photo change and **every AI edit is in the undo history** (marked "AI").

- **Critique** – strengths, problems, what to change; no edits.
- **Auto-edit** – applies an edit and explains each change.
- **Research** – uses the API's web search tool (max 5 searches) to learn how
  photographers edit this genre/light, applies a fitting edit, saves a preset with notes
  and source URLs.
- **Best-of-N** – asks for 4–6 genuinely different edits, renders them, shows Claude a
  side-by-side sheet to rank, shows you the top 3. Your pick is applied and logged to
  `.cache/ai_preferences.json`, which is included in future requests so it learns your taste.
- Without an API key the panel shows how to add one. The model name is in
  `backend/app/config.py` (`CLAUDE_MODEL`, overridable with `CCS_CLAUDE_MODEL`).

### Video
- Add clips from the library; a 720p H.264 proxy is made for playback (this is also what
  makes `.MTS` play in the browser).
- Single-track timeline: reorder (drag, or Move ◀ ▶), trim (in/out, or `[` `]` at the
  player position), split at playhead (`S`), speed ×0.25–×4, title overlays on clips,
  title cards, photos on the timeline with a slow Ken-Burns zoom, music track with volume
  and fade in/out (clip audio optionally kept underneath).
- **Looks:** any photo preset/LUT can be applied to a clip. The preset is baked into a
  single `.cube` and applied with FFmpeg `lut3d`, so clips match the photo filters.
- **Split into shots** (PySceneDetect content detector).
- **AI: make a 30 s reel** – samples 1 frame/second into a labelled contact sheet, asks
  Claude for a cut list (segments, speeds, title) and rebuilds the timeline from it.
- **Render draft (720p)** for a quick look; **Export** 9:16 (1080×1920), 16:9 (1920×1080)
  or 1:1 (1080×1080) from the original files, H.264 + AAC, into `exports/`.

### Background jobs
Exports, mockup ZIPs, video renders, scene detection and all AI requests run in a job
queue; the **Jobs** button in the header shows progress and results.

---

## Keyboard shortcuts

| Keys | Where | Action |
|---|---|---|
| `L` `E` `M` `V` | everywhere | Library / Edit / Mockups / Video |
| `?` | everywhere | Shortcut help |
| `←` `→` `↑` `↓` | Library | Move selection (Shift-click / Cmd-click to multi-select, Cmd+A all) |
| `Enter` | Library | Open (photos → Edit, videos → Video) |
| `1`–`5`, `0` | Library | Rate the selected photos / clear |
| `F` | Library | Cycle all / photos / videos |
| `\` (hold) or press on the image | Edit | Before / after |
| `←` `→` | Edit | Previous / next filter |
| `[` `]` | Edit | Previous / next photo |
| `Cmd/Ctrl+Z`, `Shift+Cmd/Ctrl+Z` | Edit | Undo / redo |
| `A` / `R` / `C` / `Esc` | Edit | Auto / Reset / Crop tool / leave crop |
| `Cmd/Ctrl+C`, `Cmd/Ctrl+V` | Edit | Copy settings / paste onto the selected photos |
| `Cmd/Ctrl+E` | Edit, Mockups, Video | Export |
| double-click a slider label | Edit | Reset that slider |
| `←` `→` / `↑` `↓` / `F` | Mockups | Next mockup / next photo / favourite |
| `Space` | Video | Play / pause |
| `S` / `Delete` | Video | Split at playhead / remove clip |
| `←` `→` (`Shift`: 0.1 s) | Video | Move playhead |
| `[` `]` | Video | Set in / out point at the player position |

---

## Running and developing

```bash
./run.sh            # Mac/Linux: installs everything with uv, starts the app, opens the browser
run.bat             # Windows
uv run pytest -q    # backend tests (generates synthetic fixtures into ./samples)
```

- Backend: `backend/app` (FastAPI, served at http://127.0.0.1:8765; API docs at `/docs`).
  `uv run python -m backend.app.main --port 9000` to change the port.
- Frontend: `frontend/` (React + Vite + TypeScript + Tailwind). `npm install && npm run dev`
  for a dev server with API proxy, `npm run build` to refresh the committed `frontend/dist`.
- Regenerate the built-in filters / mockups: `uv run python -m backend.app.core.presets`,
  `uv run python -m backend.app.core.mockups`.
- `.env` holds `ANTHROPIC_API_KEY` (copy `.env.example`; `.env` is git-ignored).
- Caches (proxies, thumbnails, video proxies, library DB, user presets, AI preferences)
  live in `.cache/`; delete it any time.

## Layout

```
backend/app/core/      pipeline, images (raw/proxy/EXIF), luts, presets, mockups, auto,
                       sidecar, library (SQLite), jobs, video, ffmpeg
backend/app/services/  ai.py (Claude tools + 4 modes, injectable client)
backend/app/api/       FastAPI routers;  backend/app/main.py serves the UI
backend/tests/         pytest suite, synthetic fixture generator, fake Anthropic client
frontend/src/          screens/ (Library, Editor, Mockups, Video), components/, api.ts, store.ts
presets/ luts/ mockups/  the 25 built-in filters and 8 templates (committed)
samples/               test media (the .ARW is downloaded, not committed)
test_output/           before/after images and clips written by the tests
```
