"""The edit settings model. One schema shared by sidecars, presets, AI tools and video looks."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

HSL_BANDS = ["red", "orange", "yellow", "green", "aqua", "blue", "purple", "magenta"]
ASPECT_PRESETS: dict[str, float | None] = {
    "free": None,
    "original": None,
    "1:1": 1.0,
    "4:5": 4 / 5,
    "9:16": 9 / 16,
    "3:2": 3 / 2,
    "16:9": 16 / 9,
}


class WhiteBalance(BaseModel):
    temp: float = 0.0  # -100 (cool) .. 100 (warm), relative to camera WB
    tint: float = 0.0  # -100 (green) .. 100 (magenta)


class HSL(BaseModel):
    hue: list[float] = Field(default_factory=lambda: [0.0] * 8)
    sat: list[float] = Field(default_factory=lambda: [0.0] * 8)
    lum: list[float] = Field(default_factory=lambda: [0.0] * 8)


class LutSetting(BaseModel):
    name: str | None = None  # file stem inside luts/
    strength: float = 1.0  # 0..1


class Crop(BaseModel):
    x: float = 0.0  # fractions of the (rotated) image
    y: float = 0.0
    w: float = 1.0
    h: float = 1.0
    rotate: float = 0.0  # degrees, clockwise positive
    aspect: str = "free"


class EditSettings(BaseModel):
    wb: WhiteBalance = Field(default_factory=WhiteBalance)
    exposure: float = 0.0  # EV, -5..5
    contrast: float = 0.0  # -100..100
    highlights: float = 0.0
    shadows: float = 0.0
    whites: float = 0.0
    blacks: float = 0.0
    curve: list[list[float]] = Field(default_factory=lambda: [[0.0, 0.0], [1.0, 1.0]])
    hsl: HSL = Field(default_factory=HSL)
    vibrance: float = 0.0
    saturation: float = 0.0
    lut: LutSetting = Field(default_factory=LutSetting)
    clarity: float = 0.0  # local contrast (unsharp, large radius)
    clahe: float = 0.0  # local contrast (CLAHE on luminance), 0..100
    denoise: float = 0.0  # 0..100
    sharpen: float = 0.0  # 0..100
    crop: Crop = Field(default_factory=Crop)
    vignette: float = 0.0  # -100..100
    lens_correction: bool = False

    def is_identity(self) -> bool:
        return self == EditSettings()

    def merged(self, partial: dict[str, Any]) -> "EditSettings":
        """Return a copy with a partial (possibly nested) dict applied on top."""
        base = self.model_dump()
        _deep_update(base, partial)
        return EditSettings.model_validate(base)


def _deep_update(base: dict, upd: dict) -> None:
    for k, v in upd.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v


# Slider limits used by the API and the AI tool schema.
SLIDER_RANGES: dict[str, tuple[float, float]] = {
    "exposure": (-5, 5),
    "contrast": (-100, 100),
    "highlights": (-100, 100),
    "shadows": (-100, 100),
    "whites": (-100, 100),
    "blacks": (-100, 100),
    "vibrance": (-100, 100),
    "saturation": (-100, 100),
    "clarity": (-100, 100),
    "clahe": (0, 100),
    "denoise": (0, 100),
    "sharpen": (0, 100),
    "vignette": (-100, 100),
    "wb.temp": (-100, 100),
    "wb.tint": (-100, 100),
}
