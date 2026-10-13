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
