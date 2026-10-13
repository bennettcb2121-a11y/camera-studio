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
