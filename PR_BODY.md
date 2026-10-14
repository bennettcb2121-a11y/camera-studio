## Charlie's Camera Studio v1 — photo + video editor for the Sony a6000

Branch `build/v1` → `main`. Local-only app: FastAPI backend (uv-managed Python 3.12,
bundled FFmpeg via imageio-ffmpeg) + pre-built React/Vite/Tailwind UI. Start with
`./run.sh` / `run.bat`; only `uv` needs to be installed.

### What works
- **Library** – folder picker (native + in-app), thumbnails (raw embedded JPEG), stars, photo/video filter, sort, multi-select, keyboard navigation.
- **Photo editor** – one float32 linear pipeline for previews/exports/mockups/video looks (WB → exposure → highlights/shadows → whites/blacks → curve → HSL/vibrance/sat → LUT w/ strength → clarity+CLAHE → denoise → sharpen → crop/rotate → vignette). 2048px cached proxy, 30–110 ms per slider change, histogram, tone curve, crop presets (1:1, 4:5, 9:16, 3:2, …), before/after on `\`, undo/redo with history list, copy/paste settings across photos, export JPEG/PNG/16-bit TIFF (export vs preview mean diff < 2%, tested on a real ILCE-6000 ARW).
- **Filters** – 25 original generated `.cube` LUT filters in Film/Travel/Portrait/Sport/Utility with live thumbnail strip, arrow-key cycling, strength; user presets. **Auto** = gray-world/camera WB blend, histogram exposure, CLAHE, ISO denoise, lensfun.
- **Mockups** – 8 procedurally generated templates (framed print, gallery wall ×3, phone, laptop, IG post, IG 3×3, poster, magazine), perspective warp + multiply overlays, favourites, ZIP export, drag-4-corners template editor.
- **AI panel** – Critique / Auto-edit / Research (web_search, max_uses 5, saves preset with sources) / Best-of-N (renders 4–6 variants, Claude ranks from a comparison sheet, top 3 shown, picks logged as a taste profile). Tools: set_adjustments, apply_filter, crop, suggest_mockups, save_preset. Every AI edit is an undoable history entry.
- **Video** – 720p proxies (MTS→mp4), single-track timeline (reorder, trim, split, speed, titles, title cards, photos with Ken-Burns, music with fades), looks via baked LUT + FFmpeg `lut3d`, PySceneDetect shots, AI 30 s reel from a 1 fps contact sheet, exports 9:16 / 16:9 / 1:1 from originals via MoviePy v2.
- **Jobs** – background queue with progress for exports, ZIPs, renders, AI.

### Tests
`uv run pytest -q` → 51 passed, 1 skipped (live API). Covers all seven acceptance checks; AI runs against a scripted fake client because no key was available. Before/after images and rendered clips are in `test_output/` (git-ignored).

### Docs
- [DECISIONS.md](DECISIONS.md) – every autonomous choice and why.
- [SETUP_FOR_CHARLIE.md](SETUP_FOR_CHARLIE.md) – install steps, what was mocked, known issues, next improvements.
- [README.md](README.md) – every feature and keyboard shortcut.
- [ENVIRONMENT.md](ENVIRONMENT.md) – build environment.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
