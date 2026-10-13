# Decisions log

Choices made autonomously during the build, with reasons. Newest at the bottom.

1. **Ran on Charlie's Mac, not a cloud box.** The session's working directory was a
   local, empty, non-git folder. Initialised git here (`main`), build work happens on
   `build/v1`. Nothing outside this folder (and the uv cache) was touched.
2. **One real raw sample.** raw.pixls.us only hosts a single ILCE-6000 file
   (`DSC01542.ARW`). It is downloaded to `samples/` and git-ignored (25 MB). Extra
   test images (JPG, portrait/landscape/low-light cases) and test clips are generated
   synthetically by `backend/tests/make_fixtures.py`.
3. **No `gh` CLI.** The PR is opened via the GitHub REST API using the SSH-authenticated
   git remote plus a token if present; otherwise a ready-to-click compare URL is printed.
4. **lensfunpy kept as a core dependency** because it installed cleanly on macOS arm64.
   The lens-correction step degrades to a no-op (and logs) if the import fails on another
   machine, so Windows installs still work.
5. **FFmpeg via imageio-ffmpeg.** `app/core/ffmpeg.py` returns the system `ffmpeg` if
   found on PATH, otherwise the bundled binary. MoviePy is pointed at the same binary.
6. **AI mocked when no key.** `ANTHROPIC_API_KEY` was unset. The AI service accepts an
   injectable client; tests use a scripted fake that returns tool calls, so Critique /
   Auto-edit / Research / Best-of-N all exercise the real tool-application code path.
7. **Raw baseline exposure +0.7 EV.** libraw's `no_auto_bright` output is ~0.7 EV darker
   than the camera's own JPEG of the same ARW (measured on the sample). The raw loader
   applies +0.7 EV so "exposure 0" looks like the camera rendering; the slider is relative.
8. **Preview speed.** Every single-slider change renders the 2048px proxy in 30–110 ms on
   this Mac (OpenCV multithreaded paths; 3D LUTs via two `cv2.remap` calls). Worst case
   with *every* stage active plus a LUT is ~245 ms, so the UI renders at 1024px while a
   slider is being dragged and at 2048px on release.
9. **Display-referred stages.** Tone curve, HSL, LUT, clarity, denoise and sharpen are
   defined on sRGB-encoded values (as .cube LUTs and photo-editor sliders are). The
   pipeline encodes once before that block and decodes once after; the image between
   stages is always linear float32 as required.
10. **CLAHE lives in the local-contrast stage.** "Auto" asks for CLAHE on luminance, but all
    edits must be slider JSON, so `clahe` (0–100) is a setting applied inside the
    local-contrast stage next to clarity. Everything stays re-renderable from the sidecar.
11. **Video looks are baked LUTs.** A preset = sliders + LUT; to apply it to a clip "via
    FFmpeg lut3d" the per-pixel part of the pipeline (WB, exposure, tone, HSL, LUT…) is
    pushed through an identity lattice and written as one .cube. Spatial stages (clarity,
    CLAHE, denoise, sharpen, vignette, crop) can't be a LUT and are skipped for video.
12. **FFmpeg filter paths.** The project folder contains an apostrophe and spaces, which
    breaks FFmpeg filtergraph quoting. `lut3d` runs with `cwd` = the LUT folder and a bare
    hash filename. Found by using the UI, now covered by a regression test.
13. **No GitHub access from this session.** No `gh`, no token, no stored HTTPS credential
    and the repo does not exist under the account's public repos (SSH auth works, but SSH
    cannot create a repo or a PR). Everything is committed on `build/v1` locally;
    `open_pr.sh` pushes and opens the PR page once a remote exists. See SETUP_FOR_CHARLIE.md.
14. **Preview quality tiers.** While a slider is being dragged the UI asks for a 1024px
    render (≈30–60 ms); on release it asks for the full 2048px proxy and writes the history
    entry. One history entry per drag, not per mouse move.
15. **Titles are drawn with Pillow, not MoviePy's TextClip** (no ImageMagick dependency;
    picks Arial/Helvetica on Mac, Arial on Windows, DejaVu on Linux, else Pillow's default).
16. **Music fixture is AAC/.m4a**: the bundled FFmpeg has no MP3 *encoder* (it decodes MP3 fine,
    so Charlie's own .mp3 tracks work).
17. **CI added after the PR was opened** (`.github/workflows/tests.yml`): pytest on
    macOS + Windows, plus a check that `frontend/dist` matches the sources. The first
    Windows run failed only on the 150 ms preview-timing assertion (shared runners are
    several times slower than the laptop); it is relaxed to 900 ms when `CI` is set and
    unchanged locally. On failure the workflow posts the pytest tail as a PR comment,
    because job logs need repo-admin auth that these sessions don't have.
18. **Vignette sign = Lightroom** (negative darkens edges). Found when Opus asked for
    "a soft vignette" with -28 and the edges got brighter. Flipped the pipeline and
    stated the convention in the AI system prompt.
19. **AI runs on Charlie's Claude login, not an API key.** The Claude Agent SDK
    (`claude-agent-sdk`) ships its own `claude` binary, so `uv sync` installs it; the app
    uses the login from `claude auth login` (run.sh offers it once). Model: Opus 5.5
    (`claude-opus-5-5`, effort "medium"). The edit tools are in-process MCP tools, Research
    uses Claude's built-in WebSearch rationed to 5 calls by a `can_use_tool` callback,
    Best-of-N and the reel cut list use structured JSON output. claude.ai connectors and
    user settings are switched off for these sessions (`strict_mcp_config`,
    `setting_sources=[]`). The API-key path still works (`CCS_AI_BACKEND=api`).
20. **Opus can look at its own work** (`view_photo` tool returns the current render as an
    image), so Ask/Auto-edit are edit → look → refine loops instead of blind slider guesses.
