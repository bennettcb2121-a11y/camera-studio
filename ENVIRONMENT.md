# Environment (checked 2026-10-13)

This session ran directly on Charlie's Mac (not a cloud VM), in
`/Users/charliebennett/Charlie's Camera Studio`, which was an empty, non-git folder.

| Item | Result |
|---|---|
| OS / CPU | macOS 26.6.2, Apple Silicon (arm64), 11 cores, 18 GB RAM, 171 GB free |
| Python | System 3.9.6 only; **uv 0.12.2** present → uv installed CPython 3.12 for the project |
| Node | v20.20.0, npm 10.8.2 (used only to build the frontend; built files are committed) |
| FFmpeg | Not installed system-wide → using `imageio-ffmpeg` (bundles FFmpeg 7.1 for macOS arm64) |
| git | 2.50.1, SSH auth to GitHub works (`bennettcb2121-a11y`) |
| gh CLI | Not installed → PR is opened via the GitHub REST API or a manual URL (see SETUP_FOR_CHARLIE.md) |
| Network | PyPI, npm, GitHub, raw.pixls.us all reachable |
| ANTHROPIC_API_KEY | **Not set** → AI features tested against a mocked client |
| ./samples | Empty → downloaded 1 real Sony ILCE-6000 ARW (`DSC01542.ARW`, 24 MP) from raw.pixls.us; synthetic JPG/video generated for the rest |
| lensfun | `lensfunpy` 1.18 installs cleanly and knows the ILCE-6000 |

Verified imports: rawpy 0.27, numpy 2.2, opencv 5.0, colour-science 0.4.7,
moviepy 2.1.2, scenedetect 0.7.2, anthropic SDK 1.13, fastapi 0.143.
