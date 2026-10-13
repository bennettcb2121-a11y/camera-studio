"""Video: proxies, project/timeline model, scene detection, looks via FFmpeg lut3d,
MoviePy v2 rendering, export presets.

A project is JSON in .cache/video_projects/<id>.json:
{
  "id", "name", "fps": 25,
  "clips": [
     {"id", "type": "video", "src", "in": 0.0, "out": 4.2, "speed": 1.0,
      "look": {"preset_id": "golden-hour", "strength": 1.0} | null, "title": {...}|null},
     {"id", "type": "photo", "src", "duration": 4.0, "zoom": true, "look": ..., "title": ...},
     {"id", "type": "title", "text": "...", "duration": 3.0, "bg": "#000000", "color": "#ffffff", "size": 72}
  ],
  "music": {"src": "...", "volume": 0.8, "fade_in": 1.0, "fade_out": 2.0} | null
}
Titles on video/photo clips: {"text", "position": "center"|"bottom"|"top", "size": 56, "color": "#fff", "duration": null (whole clip)}
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..config import DATA_DIR, VIDEO_PROXY_DIR
from . import color, ffmpeg
from .images import cache_key, load_linear, proxy_cache, resize_long_edge
from .luts import Lut3D
from .pipeline import render, finalize
from .presets import get_preset
from .settings_schema import EditSettings

ffmpeg.configure_moviepy()

PROJECTS_DIR = DATA_DIR / "video_projects"
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
RENDER_DIR = DATA_DIR / "video_renders"
RENDER_DIR.mkdir(parents=True, exist_ok=True)
LOOK_LUT_DIR = DATA_DIR / "video_luts"
LOOK_LUT_DIR.mkdir(parents=True, exist_ok=True)

EXPORT_PRESETS = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
}
PROXY_HEIGHT = 720


# --------------------------------------------------------------------------- proxies
def proxy_path(src: Path) -> Path:
    return VIDEO_PROXY_DIR / f"{cache_key(src)}.mp4"


def ensure_proxy(src: Path, progress=None) -> Path:
    """720p H.264 proxy with AAC audio (MTS/AVCHD -> mp4 is what makes browsers play it)."""
    out = proxy_path(src)
    if out.exists() and out.stat().st_size > 0:
        return out
    tmp = out.with_suffix(".tmp.mp4")
    r = ffmpeg.run(["-i", str(src), "-vf", f"scale=-2:{PROXY_HEIGHT}", "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                    "-f", "mp4", str(tmp)])
    if r.returncode != 0:
        raise RuntimeError(f"proxy failed: {r.stderr[-800:]}")
    tmp.replace(out)
    return out


def clip_thumbnail(src: Path, t: float = 0.5, width: int = 320) -> bytes:
    r = subprocess.run([ffmpeg.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-ss", f"{max(0, t):.3f}", "-i", str(src),
                        "-frames:v", "1", "-vf", f"scale={width}:-2", "-f", "image2", "-c:v", "mjpeg", "-"],
                       capture_output=True)
    return r.stdout


def probe(src: Path) -> dict:
    return ffmpeg.probe(str(src))


# --------------------------------------------------------------------------- projects
def _ppath(pid: str) -> Path:
    return PROJECTS_DIR / f"{pid}.json"


def new_project(name: str = "Untitled reel") -> dict:
    pid = uuid.uuid4().hex[:10]
    proj = {"id": pid, "name": name, "fps": 25, "clips": [], "music": None, "created": time.time()}
    save_project(proj)
    return proj


def list_projects() -> list[dict]:
    out = []
    for p in sorted(PROJECTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            d = json.loads(p.read_text())
            out.append({"id": d["id"], "name": d.get("name"), "clips": len(d.get("clips", [])), "created": d.get("created")})
        except Exception:
            pass
    return out


def load_project(pid: str) -> dict:
    p = _ppath(pid)
    if not p.exists():
        raise KeyError(pid)
    return json.loads(p.read_text())


def save_project(proj: dict) -> dict:
    proj["duration"] = project_duration(proj)
    _ppath(proj["id"]).write_text(json.dumps(proj, indent=1))
    return proj


def delete_project(pid: str) -> bool:
    p = _ppath(pid)
    if p.exists():
        p.unlink()
        return True
    return False


def clip_duration(c: dict) -> float:
    if c["type"] == "video":
        return max(0.0, (c["out"] - c["in"]) / max(float(c.get("speed", 1.0)), 0.01))
    return float(c.get("duration", 3.0))


def project_duration(proj: dict) -> float:
    return float(sum(clip_duration(c) for c in proj.get("clips", [])))


def add_video_clip(proj: dict, src: Path, start: float | None = None, end: float | None = None) -> dict:
    info = probe(src)
    c = {"id": uuid.uuid4().hex[:8], "type": "video", "src": str(src), "in": float(start or 0.0),
         "out": float(end if end is not None else info["duration"]), "speed": 1.0, "look": None, "title": None,
         "src_duration": info["duration"], "has_audio": info.get("has_audio", True)}
    proj["clips"].append(c)
    return c


def add_photo_clip(proj: dict, src: Path, duration: float = 4.0, zoom: bool = True) -> dict:
    c = {"id": uuid.uuid4().hex[:8], "type": "photo", "src": str(src), "duration": float(duration), "zoom": zoom,
         "look": None, "title": None}
    proj["clips"].append(c)
    return c


def add_title_clip(proj: dict, text: str, duration: float = 3.0, size: int = 72, color: str = "#ffffff", bg: str = "#000000") -> dict:
    c = {"id": uuid.uuid4().hex[:8], "type": "title", "text": text, "duration": float(duration), "size": size,
         "color": color, "bg": bg}
    proj["clips"].append(c)
    return c


def find_clip(proj: dict, cid: str) -> tuple[int, dict]:
    for i, c in enumerate(proj["clips"]):
        if c["id"] == cid:
            return i, c
    raise KeyError(cid)


def split_at(proj: dict, playhead: float) -> dict | None:
    """Split the clip under the playhead (project time) into two."""
    t = 0.0
    for i, c in enumerate(proj["clips"]):
        d = clip_duration(c)
        if t < playhead < t + d - 0.05:
            local = playhead - t
            if c["type"] == "video":
                cut = c["in"] + local * float(c.get("speed", 1.0))
                a = dict(c, id=uuid.uuid4().hex[:8], out=cut)
                b = dict(c, id=uuid.uuid4().hex[:8], **{"in": cut})
            else:
                a = dict(c, id=uuid.uuid4().hex[:8], duration=local)
                b = dict(c, id=uuid.uuid4().hex[:8], duration=d - local)
            proj["clips"][i : i + 1] = [a, b]
            return b
        t += d
    return None


# --------------------------------------------------------------------------- looks
_SPATIAL_FIELDS = {"clarity", "clahe", "denoise", "sharpen", "vignette", "crop"}


def bake_look_cube(settings: EditSettings, size: int = 33) -> Path:
    """Bake the per-pixel part of an edit (WB, exposure, tone, HSL, LUT...) into one
    .cube by pushing an identity lattice through the photo pipeline. Spatial stages
    (clarity, denoise, sharpen, vignette, crop) cannot live in a LUT and are skipped."""
    s = settings.model_dump()
    for k in _SPATIAL_FIELDS:
        s.pop(k, None)
    s = EditSettings.model_validate(s)
    key = __import__("hashlib").sha1(json.dumps(s.model_dump(), sort_keys=True).encode()).hexdigest()[:16]
    out = LOOK_LUT_DIR / f"{key}.cube"
    if out.exists():
        return out
    ident = Lut3D.identity(size).table.reshape(size * size, size, 3)  # as an "image"
    lin = color.decode(ident)  # pipeline wants linear input
    res = render(lin, s, 1.0)
    enc = color.encode(np.clip(res, 0, 1))
    Lut3D(enc.reshape(size, size, size, 3), f"look-{key}").write_cube(out)
    return out


def look_settings(look: dict | None) -> EditSettings | None:
    if not look:
        return None
    if look.get("preset_id"):
        rec = get_preset(look["preset_id"])
        if not rec:
            return None
        s = EditSettings().merged({k: v for k, v in rec["settings"].items() if k not in ("crop",)})
        if look.get("strength") is not None and s.lut.name:
            s = s.merged({"lut": {"strength": float(look["strength"])}})
        return s
    if look.get("settings"):
        return EditSettings().merged(look["settings"])
    return None


def apply_look_ffmpeg(src: Path, cube: Path, start: float, end: float, out: Path, height: int | None = None) -> Path:
    """Trim + lut3d in one FFmpeg pass (this is how the video look matches the photo look)."""
    # The filtergraph parser chokes on quotes, colons and spaces in paths (e.g. a folder
    # called "Charlie's Camera Studio"), so run with cwd = the LUT folder and pass only
    # its plain hash filename. Input/output paths are ordinary args and are safe.
    vf = f"lut3d={cube.name}"
    if height:
        vf += f",scale=-2:{height}"
    args = ["-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(Path(src).resolve()), "-vf", vf, "-c:v", "libx264",
            "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
            "-f", "mp4", str(Path(out).resolve())]
    r = ffmpeg.run(args, cwd=str(cube.parent))
    if r.returncode != 0:
        raise RuntimeError(f"lut3d failed: {r.stderr[-800:]}")
    return out


# --------------------------------------------------------------------------- scene detection
def detect_scenes(src: Path, threshold: float = 27.0) -> list[tuple[float, float]]:
    from scenedetect import ContentDetector, detect

    proxy = ensure_proxy(src)
    scenes = detect(str(proxy), ContentDetector(threshold=threshold))
    out = [(s.get_seconds(), e.get_seconds()) for s, e in scenes]
    if not out:
        out = [(0.0, probe(src)["duration"])]
    return out


def split_into_shots(proj: dict, cid: str) -> list[dict]:
    i, c = find_clip(proj, cid)
    if c["type"] != "video":
        return [c]
    scenes = detect_scenes(Path(c["src"]))
    new = []
    for s, e in scenes:
        s2, e2 = max(s, c["in"]), min(e, c["out"])
        if e2 - s2 > 0.2:
            new.append(dict(c, id=uuid.uuid4().hex[:8], **{"in": s2, "out": e2}))
    if new:
        proj["clips"][i : i + 1] = new
    return new


# --------------------------------------------------------------------------- contact sheet (AI reel)
def contact_sheet(src: Path, fps: float = 1.0, thumb_w: int = 240, cols: int = 6) -> tuple[bytes, list[float]]:
    """Sample 1 frame/second into a labelled grid JPEG. Returns (jpeg bytes, timestamps)."""
    proxy = ensure_proxy(src)
    tmp = Path(tempfile.mkdtemp(prefix="ccs_sheet_"))
    r = ffmpeg.run(["-i", str(proxy), "-vf", f"fps={fps},scale={thumb_w}:-2", str(tmp / "f%04d.jpg")])
    frames = sorted(tmp.glob("f*.jpg"))
    if not frames:
        raise RuntimeError(f"no frames sampled: {r.stderr[-300:]}")
    ims = [Image.open(f).convert("RGB") for f in frames]
    w, h = ims[0].size
    rows = (len(ims) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w, rows * (h + 16)), (10, 10, 10))
    d = ImageDraw.Draw(sheet)
    ts = []
    for i, im in enumerate(ims):
        x, y = (i % cols) * w, (i // cols) * (h + 16)
        sheet.paste(im, (x, y + 16))
        t = i / fps
        ts.append(t)
        d.text((x + 4, y + 2), f"#{i} t={t:.0f}s", fill=(255, 255, 0))
    for f in frames:
        f.unlink()
    tmp.rmdir()
    import io

    buf = io.BytesIO()
    sheet.save(buf, "JPEG", quality=80)
    return buf.getvalue(), ts


# --------------------------------------------------------------------------- rendering
def _font(size: int):
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc", "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for c in candidates:
        if os.path.exists(c):
            try:
                return ImageFont.truetype(c, size)
            except Exception:
                pass
    return ImageFont.load_default(size=size) if hasattr(ImageFont, "load_default") else ImageFont.load_default()


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return tuple(int(c[i : i + 2], 16) for i in (0, 2, 4))


def render_title_image(text: str, size: tuple[int, int], font_size: int = 72, color: str = "#ffffff",
                       bg: str | None = None, position: str = "center") -> np.ndarray:
    """RGBA title card (or transparent overlay when bg is None), word-wrapped."""
    w, h = size
    im = Image.new("RGBA", (w, h), (*_hex(bg), 255) if bg else (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    font = _font(font_size)
    words = text.split()
    lines, cur = [], ""
    for wd in words:
        trial = (cur + " " + wd).strip()
        if d.textlength(trial, font=font) > w * 0.85 and cur:
            lines.append(cur)
            cur = wd
        else:
            cur = trial
    if cur:
        lines.append(cur)
    lh = int(font_size * 1.25)
    total = lh * len(lines)
    y0 = {"top": int(h * 0.08), "bottom": h - total - int(h * 0.08)}.get(position, (h - total) // 2)
    for i, line in enumerate(lines):
        tw = d.textlength(line, font=font)
        x = (w - tw) / 2
        y = y0 + i * lh
        d.text((x + 3, y + 3), line, font=font, fill=(0, 0, 0, 160))
        d.text((x, y), line, font=font, fill=(*_hex(color), 255))
    return np.array(im)


def _photo_frames(src: Path, out_size: tuple[int, int], duration: float, zoom: bool, settings: EditSettings | None):
    """Return a frame function for a photo with a slow Ken Burns zoom."""
    W, H = out_size
    lin, _, fw = (None, None, None)
    proxy, full_w, full_h = proxy_cache.get(src)
    img = finalize(render(proxy, settings or EditSettings(), proxy.shape[1] / full_w))
    ih, iw = img.shape[:2]
    # cover-fit the frame
    scale = max(W / iw, H / ih) * 1.0
    base = cv2.resize(img, (int(round(iw * scale)) + 2, int(round(ih * scale)) + 2), interpolation=cv2.INTER_AREA)
    bh, bw = base.shape[:2]

    def frame(t: float):
        z = 1.0 + (0.08 * (t / max(duration, 0.01)) if zoom else 0.0)
        cw, ch = int(W / z), int(H / z)
        x0, y0 = (bw - cw) // 2, (bh - ch) // 2
        crop = base[y0 : y0 + ch, x0 : x0 + cw]
        return cv2.resize(crop, (W, H), interpolation=cv2.INTER_LINEAR)

    return frame


def render_project(proj: dict, out_path: Path, aspect: str = "16:9", draft: bool = False, progress=None) -> Path:
    """Assemble the timeline with MoviePy v2 and write an H.264 MP4.

    draft=True renders from the 720p proxies at 720p height (fast preview); otherwise
    from the originals at the export preset size. Looks are applied by FFmpeg lut3d
    before MoviePy sees the clip, so they are bit-for-bit the same transform as photos."""
    from moviepy import AudioFileClip, CompositeVideoClip, ImageClip, VideoClip, VideoFileClip, afx, concatenate_videoclips, vfx

    W, H = EXPORT_PRESETS.get(aspect, EXPORT_PRESETS["16:9"])
    if draft:
        k = PROXY_HEIGHT / max(W, H) if aspect == "9:16" else PROXY_HEIGHT / H
        W, H = int(W * k) // 2 * 2, int(H * k) // 2 * 2
    fps = int(proj.get("fps", 25))
    tmpdir = Path(tempfile.mkdtemp(prefix="ccs_render_"))
    clips = []
    opened = []
    n = max(len(proj["clips"]), 1)
    try:
        for i, c in enumerate(proj["clips"]):
            if progress:
                progress(0.05 + 0.5 * i / n, f"Preparing clip {i + 1}/{n}")
            dur = clip_duration(c)
            if dur <= 0.01:
                continue
            look = look_settings(c.get("look"))
            if c["type"] == "video":
                src = ensure_proxy(Path(c["src"])) if draft else Path(c["src"])
                if look is not None:
                    cube = bake_look_cube(look)
                    src = apply_look_ffmpeg(src, cube, c["in"], c["out"], tmpdir / f"look_{c['id']}.mp4",
                                            height=PROXY_HEIGHT if draft else None)
                    v = VideoFileClip(str(src))
                else:
                    v = VideoFileClip(str(src)).subclipped(c["in"], c["out"])
                opened.append(v)
                speed = float(c.get("speed", 1.0))
                if speed != 1.0:
                    v = v.with_effects([vfx.MultiplySpeed(speed)])
                # cover-fit into the export frame
                sw, sh = v.size
                s = max(W / sw, H / sh)
                v = v.resized((int(round(sw * s)) // 2 * 2 or 2, int(round(sh * s)) // 2 * 2 or 2))
                v = v.cropped(x_center=v.w / 2, y_center=v.h / 2, width=W, height=H)
            elif c["type"] == "photo":
                frame_fn = _photo_frames(Path(c["src"]), (W, H), dur, bool(c.get("zoom", True)), look)
                v = VideoClip(frame_fn, duration=dur)
            else:  # title card
                arr = render_title_image(c.get("text", ""), (W, H), int(c.get("size", 72)), c.get("color", "#ffffff"),
                                         c.get("bg", "#000000"))
                v = ImageClip(arr[..., :3]).with_duration(dur)
            title = c.get("title")
            if title and title.get("text"):
                tdur = float(title.get("duration") or dur)
                overlay = render_title_image(title["text"], (W, H), int(title.get("size", 56)), title.get("color", "#ffffff"),
                                             None, title.get("position", "bottom"))
                tclip = ImageClip(overlay[..., :3]).with_mask(ImageClip(overlay[..., 3] / 255.0, is_mask=True))
                tclip = tclip.with_duration(min(tdur, dur)).with_start(0)
                v = CompositeVideoClip([v, tclip], size=(W, H)).with_duration(dur)
            clips.append(v.with_fps(fps))
        if not clips:
            raise ValueError("timeline is empty")
        final = concatenate_videoclips(clips, method="compose")
        music = proj.get("music")
        if music and music.get("src") and Path(music["src"]).exists():
            a = AudioFileClip(music["src"])
            opened.append(a)
            total = final.duration
            if a.duration > total:
                a = a.subclipped(0, total)
            a = a.with_effects([afx.MultiplyVolume(float(music.get("volume", 0.8))),
                                afx.AudioFadeIn(float(music.get("fade_in", 1.0))),
                                afx.AudioFadeOut(float(music.get("fade_out", 2.0)))])
            if final.audio is not None and music.get("keep_clip_audio", True):
                from moviepy import CompositeAudioClip

                final = final.with_audio(CompositeAudioClip([final.audio, a]))
            else:
                final = final.with_audio(a)
        if progress:
            progress(0.6, "Encoding")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        final.write_videofile(str(out_path), fps=fps, codec="libx264", audio_codec="aac", preset="veryfast" if draft else "medium",
                              ffmpeg_params=["-crf", "23" if draft else "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart"],
                              threads=os.cpu_count() or 4, logger=None)
        if progress:
            progress(1.0, "Done")
        return out_path
    finally:
        for o in opened:
            try:
                o.close()
            except Exception:
                pass
        # On Windows an FFmpeg reader may still hold a temp file for a moment; a leftover
        # temp file is harmless, so never let cleanup fail the render.
        for f in tmpdir.glob("*"):
            try:
                f.unlink()
            except OSError:
                pass
        try:
            tmpdir.rmdir()
        except OSError:
            pass
