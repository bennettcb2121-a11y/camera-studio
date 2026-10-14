from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from fastapi import HTTPException
from fastapi.responses import Response
from PIL import Image


def existing_file(p: str) -> Path:
    path = Path(p).expanduser()
    if not path.is_file():
        raise HTTPException(404, f"file not found: {p}")
    return path


def jpeg_response(rgb8: np.ndarray, quality: int = 88, headers: dict | None = None) -> Response:
    buf = io.BytesIO()
    Image.fromarray(rgb8).save(buf, "JPEG", quality=quality, subsampling=0 if quality >= 92 else 2)
    return Response(buf.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "no-store", **(headers or {})})
