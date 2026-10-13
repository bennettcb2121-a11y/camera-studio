"""Locate an FFmpeg binary: system one if on PATH, else the imageio-ffmpeg bundle."""
from __future__ import annotations

import os
import shutil
import subprocess
from functools import lru_cache


@lru_cache(maxsize=1)
def ffmpeg_exe() -> str:
    sys_ff = shutil.which("ffmpeg")
    if sys_ff:
        return sys_ff
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


@lru_cache(maxsize=1)
def ffprobe_exe() -> str | None:
    return shutil.which("ffprobe")


def configure_moviepy() -> None:
    os.environ.setdefault("FFMPEG_BINARY", ffmpeg_exe())
    os.environ.setdefault("IMAGEIO_FFMPEG_EXE", ffmpeg_exe())


def run(args: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def probe(path: str) -> dict:
    """Duration/size/fps via ffprobe when present, otherwise by parsing ffmpeg -i."""
    import json
    import re

    fp = ffprobe_exe()
    if fp:
        p = subprocess.run([fp, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path],
                           capture_output=True, text=True)
        try:
            d = json.loads(p.stdout)
            v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
            a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), None)
            num, den = (v.get("r_frame_rate", "25/1").split("/") + ["1"])[:2]
            return {"duration": float(d.get("format", {}).get("duration", 0) or 0), "width": v.get("width"),
                    "height": v.get("height"), "fps": float(num) / float(den or 1), "has_audio": a is not None}
        except Exception:
            pass
    p = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", path], capture_output=True, text=True)
    txt = p.stderr
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.?\d*)", txt)
    dur = (int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])) if m else 0.0
    m = re.search(r"Video:.*? (\d{2,5})x(\d{2,5})", txt)
    w, h = (int(m[1]), int(m[2])) if m else (None, None)
    m = re.search(r"(\d+(?:\.\d+)?) fps", txt)
    fps = float(m[1]) if m else 25.0
    return {"duration": dur, "width": w, "height": h, "fps": fps, "has_audio": "Audio:" in txt}
