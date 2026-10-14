""".cube 3D LUT reading, writing and fast trilinear application."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import cv2

import numpy as np

from ..config import LUTS_DIR


class Lut3D:
    def __init__(self, table: np.ndarray, title: str = ""):
        # table shape (N, N, N, 3), indexed [b, g, r] as in the .cube spec (red fastest).
        assert table.ndim == 4 and table.shape[3] == 3
        self.table = table.astype(np.float32)
        self.size = table.shape[0]
        self.title = title

    @classmethod
    def identity(cls, size: int = 33) -> "Lut3D":
        r = np.linspace(0, 1, size, dtype=np.float32)
        b, g, rr = np.meshgrid(r, r, r, indexing="ij")
        return cls(np.stack([rr, g, b], axis=-1))

    @classmethod
    def from_function(cls, fn, size: int = 33, title: str = "") -> "Lut3D":
        """Build a LUT by evaluating fn(rgb_array[N^3,3] in sRGB-encoded 0..1) -> same shape."""
        ident = cls.identity(size)
        flat = ident.table.reshape(-1, 3)
        out = np.clip(fn(flat.copy()), 0, 1).astype(np.float32)
        return cls(out.reshape(size, size, size, 3), title)

    @property
    def _planes(self) -> np.ndarray:
        # (N*N, N, 3) "image": row = b*N + g, column = r. Bilinear remap on this image
        # is exactly bilinear interpolation in (r, g) inside one blue plane.
        p = getattr(self, "_planes_cache", None)
        if p is None:
            p = np.ascontiguousarray(self.table.reshape(self.size * self.size, self.size, 3))
            self._planes_cache = p
        return p

    def apply(self, enc: np.ndarray) -> np.ndarray:
        """Trilinear interpolation. `enc` is sRGB-encoded float32 HxWx3 in [0,1].

        Implemented as two cv2.remap calls (one per bounding blue plane) blended by
        the blue fraction: ~18x faster than NumPy gathers at 2048px."""
        n = self.size
        x = cv2.multiply(np.ascontiguousarray(enc, dtype=np.float32), (n - 1, n - 1, n - 1, 0))
        x = np.clip(x, 0, n - 1, out=x)
        r, g, b = cv2.split(x)
        b0 = np.minimum(np.floor(b), n - 2)
        fb = cv2.subtract(b, b0)
        my0 = cv2.add(cv2.multiply(b0, float(n)), g)
        my1 = cv2.add(my0, float(n))
        planes = self._planes
        c0 = cv2.remap(planes, r, my0, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        c1 = cv2.remap(planes, r, my1, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        fb3 = cv2.merge([fb, fb, fb])
        return cv2.add(c0, cv2.multiply(cv2.subtract(c1, c0), fb3))

    def apply_exact(self, enc: np.ndarray) -> np.ndarray:
        """Reference NumPy trilinear implementation (tests only)."""
        n = self.size
        x = np.clip(enc, 0.0, 1.0) * (n - 1)
        i0 = np.minimum(np.floor(x).astype(np.int32), n - 2)
        f = (x - i0).astype(np.float32)
        r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
        fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]
        t = self.table
        c00 = t[b0, g0, r0] * (1 - fr) + t[b0, g0, r0 + 1] * fr
        c10 = t[b0, g0 + 1, r0] * (1 - fr) + t[b0, g0 + 1, r0 + 1] * fr
        c01 = t[b0 + 1, g0, r0] * (1 - fr) + t[b0 + 1, g0, r0 + 1] * fr
        c11 = t[b0 + 1, g0 + 1, r0] * (1 - fr) + t[b0 + 1, g0 + 1, r0 + 1] * fr
        c0 = c00 * (1 - fg) + c10 * fg
        c1 = c01 * (1 - fg) + c11 * fg
        return c0 * (1 - fb) + c1 * fb

    def write_cube(self, path: Path) -> None:
        n = self.size
        lines = [f'TITLE "{self.title or path.stem}"', f"LUT_3D_SIZE {n}", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
        flat = self.table.reshape(-1, 3)
        lines += [f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in flat]
        path.write_text("\n".join(lines) + "\n")

    @classmethod
    def read_cube(cls, path: Path) -> "Lut3D":
        size = None
        title = ""
        rows: list[list[float]] = []
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            up = line.upper()
            if up.startswith("TITLE"):
                title = line[5:].strip().strip('"')
            elif up.startswith("LUT_3D_SIZE"):
                size = int(line.split()[1])
            elif up.startswith("LUT_1D_SIZE"):
                raise ValueError("1D LUTs are not supported")
            elif up.startswith("DOMAIN_"):
                continue
            else:
                parts = line.split()
                if len(parts) >= 3:
                    rows.append([float(parts[0]), float(parts[1]), float(parts[2])])
        if size is None:
            raise ValueError(f"{path}: missing LUT_3D_SIZE")
        arr = np.array(rows, dtype=np.float32)
        if arr.shape[0] != size**3:
            raise ValueError(f"{path}: expected {size**3} rows, got {arr.shape[0]}")
        return cls(arr.reshape(size, size, size, 3), title)


def list_luts() -> list[str]:
    return sorted(p.stem for p in LUTS_DIR.glob("*.cube"))


def lut_path(name: str) -> Path:
    safe = Path(name).stem
    return LUTS_DIR / f"{safe}.cube"


@lru_cache(maxsize=64)
def _load(name: str, mtime: float) -> Lut3D:
    return Lut3D.read_cube(lut_path(name))


def load_lut(name: str) -> Lut3D | None:
    p = lut_path(name)
    if not p.exists():
        return None
    return _load(name, p.stat().st_mtime)
