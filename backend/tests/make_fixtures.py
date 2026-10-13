"""Generate synthetic test images and clips into ./samples when real ones are missing.

Run: uv run python backend/tests/make_fixtures.py
Creates (idempotent):
  samples/synth_landscape.jpg   - sky gradient, hills, sun: bright, colourful
  samples/synth_portrait.jpg    - warm skin-tone disc on a dark background, 3:2 portrait
  samples/synth_lowlight.jpg    - dark noisy scene (ISO 3200-like)
  samples/synth_highkey.jpg     - near-white scene with pastel shapes
  samples/synth_clip_a.mp4      - 6 s moving shapes with an audio tone, 1080p25
  samples/synth_clip_b.MTS      - 5 s different scene, 1080p25 as MPEG-TS (AVCHD container)
  samples/synth_music.m4a       - 20 s synthetic music bed (AAC; the bundled FFmpeg has no MP3 encoder)
"""
from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
SAMPLES = ROOT / "samples"
SAMPLES.mkdir(exist_ok=True)


def _exif_bytes(iso: int, shutter: tuple[int, int], fnum: float, focal: float) -> bytes:
    """Build a minimal EXIF blob so synthetic JPEGs carry ISO/shutter/aperture/lens."""
    im = Image.new("RGB", (1, 1))
    exif = im.getexif()
    exif[0x010F] = "SONY"
    exif[0x0110] = "ILCE-6000"
    ifd = exif.get_ifd(0x8769)
    ifd[0x8827] = iso
    ifd[0x829A] = (shutter[0], shutter[1])
    ifd[0x829D] = (int(fnum * 10), 10)
    ifd[0x920A] = (int(focal * 10), 10)
    ifd[0xA434] = "E 18-55mm F3.5-5.6 OSS"
    return exif.tobytes()


def _save(name: str, arr: np.ndarray, iso: int, shutter, fnum: float, focal: float) -> Path:
    p = SAMPLES / name
    if p.exists():
        return p
    Image.fromarray(np.clip(arr * 255, 0, 255).astype(np.uint8)).save(p, quality=94, exif=_exif_bytes(iso, shutter, fnum, focal))
    return p


def landscape(w=3000, h=2000):
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    u, v = xx / w, yy / h
    img = np.zeros((h, w, 3), np.float32)
    sky = np.stack([0.35 + 0.3 * v, 0.55 + 0.25 * v, 0.95 - 0.1 * v], -1)
    img[:] = sky
    sun = np.exp(-(((u - 0.72) ** 2 + (v - 0.28) ** 2) / 0.004))
    img += sun[..., None] * np.array([1.0, 0.85, 0.5], np.float32)
    hill = 0.62 + 0.08 * np.sin(u * 9) + 0.05 * np.sin(u * 23 + 1)
    m = v > hill
    img[m] = (np.stack([0.18 + 0.25 * v, 0.42 + 0.2 * v, 0.12 + 0.05 * v], -1) * (1 + 0.15 * np.sin(u * 200)[..., None]))[m]
    tree = (np.abs(u - 0.3) < 0.03) & (v > 0.5) & (v < hill + 0.05)
    img[tree] = [0.08, 0.2, 0.06]
    return img


def portrait(w=2000, h=3000):
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    u, v = xx / w, yy / h
    img = np.zeros((h, w, 3), np.float32)
    bg = 0.12 + 0.1 * (1 - v) + 0.04 * np.sin(u * 40) * np.sin(v * 40)
    img[:] = np.stack([bg * 0.9, bg * 0.85, bg], -1)
    face = ((u - 0.5) ** 2 / 0.05 + (v - 0.42) ** 2 / 0.09) < 1
    shade = 0.75 + 0.25 * (u - 0.3)
    skin = np.stack([0.85, 0.62, 0.5], -1) * shade[..., None]
    img[face] = skin[face]
    hair = (((u - 0.5) ** 2 / 0.08 + (v - 0.33) ** 2 / 0.06) < 1) & (v < 0.33)
    img[hair] = [0.15, 0.09, 0.05]
    body = (np.abs(u - 0.5) < 0.3) & (v > 0.68)
    img[body] = np.stack([0.2, 0.3, 0.55], -1) * (0.9 + 0.2 * v)[..., None][body]
    return img


def lowlight(w=3000, h=2000, seed=1):
    rng = np.random.default_rng(seed)
    base = landscape(w, h) * 0.12
    base[:, :, 0] *= 1.3  # tungsten-ish cast
    base[:, :, 2] *= 0.7
    lamp = np.exp(-(((np.linspace(0, 1, w)[None, :] - 0.5) ** 2 + (np.linspace(0, 1, h)[:, None] - 0.6) ** 2) / 0.01))
    base += lamp[..., None] * np.array([0.9, 0.7, 0.4], np.float32)
    noise = rng.normal(0, 0.04, base.shape).astype(np.float32)
    return np.clip(base + noise, 0, 1)


def highkey(w=3000, h=2000):
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    u, v = xx / w, yy / h
    img = np.full((h, w, 3), 0.93, np.float32)
    for cx, cy, r, col in [(0.3, 0.5, 0.18, (0.95, 0.75, 0.8)), (0.65, 0.4, 0.14, (0.75, 0.85, 0.98)), (0.7, 0.75, 0.1, (0.85, 0.95, 0.75))]:
        m = ((u - cx) ** 2 + ((v - cy) * h / w) ** 2) < r * r
        img[m] = col
    return img


def clip(name: str, seconds: int, hue_shift: float, container_args: list[str], fps=25, w=1920, h=1080):
    p = SAMPLES / name
    if p.exists():
        return p
    from app.core.ffmpeg import ffmpeg_exe

    frames_dir = SAMPLES / f".frames_{p.stem}"
    frames_dir.mkdir(exist_ok=True)
    n = seconds * fps
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    u, v = xx / w, yy / h
    for i in range(n):
        t = i / fps
        scene = int(t // 2)  # hard cut every 2 s so scene detection has something to find
        bgc = [(0.1, 0.15, 0.3), (0.3, 0.12, 0.1), (0.1, 0.25, 0.15)][(scene + int(hue_shift)) % 3]
        img = np.zeros((h, w, 3), np.float32)
        img[:] = bgc
        img += 0.15 * (v[..., None])
        cx = 0.2 + 0.6 * ((t / seconds) % 1)
        cy = 0.5 + 0.2 * math.sin(t * 2)
        disc = ((u - cx) ** 2 + ((v - cy) * h / w) ** 2) < 0.02
        img[disc] = [(0.9, 0.6, 0.2), (0.2, 0.8, 0.9), (0.9, 0.9, 0.3)][scene % 3]
        bar = (np.abs(u - (0.5 + 0.4 * math.cos(t))) < 0.01)
        img[bar] = [0.95, 0.95, 0.95]
        Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8)).save(frames_dir / f"f{i:04d}.png")
    cmd = [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-framerate", str(fps), "-i", str(frames_dir / "f%04d.png"),
           "-f", "lavfi", "-i", f"sine=frequency={330 + 110 * hue_shift}:duration={seconds}", "-shortest",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", *container_args, str(p)]
    subprocess.run(cmd, check=True)
    for f in frames_dir.glob("*.png"):
        f.unlink()
    frames_dir.rmdir()
    return p


def music(name="synth_music.m4a", seconds=20):
    p = SAMPLES / name
    if p.exists():
        return p
    from app.core.ffmpeg import ffmpeg_exe

    # no commas: the filtergraph parser treats them as filter separators
    expr = "0.25*sin(2*PI*220*t)*(0.6+0.4*sin(2*PI*0.5*t))+0.15*sin(2*PI*330*t)+0.1*sin(2*PI*440*t)*(0.5+0.5*sin(2*PI*t))"
    subprocess.run([ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"aevalsrc={expr}:s=44100:d={seconds}",
                    "-c:a", "aac", "-b:a", "128k", str(p)], check=True)
    return p


def main() -> list[Path]:
    out = [
        _save("synth_landscape.jpg", landscape(), 100, (1, 500), 8.0, 24),
        _save("synth_portrait.jpg", portrait(), 400, (1, 160), 2.8, 50),
        _save("synth_lowlight.jpg", lowlight(), 3200, (1, 30), 3.5, 18),
        _save("synth_highkey.jpg", highkey(), 200, (1, 250), 5.6, 35),
        clip("synth_clip_a.mp4", 6, 0, ["-movflags", "+faststart"]),
        clip("synth_clip_b.MTS", 5, 1, ["-f", "mpegts"]),
        music(),
    ]
    for p in out:
        print(p.name, p.stat().st_size)
    return out


if __name__ == "__main__":
    main()
