"""SQLite index of the chosen library folder(s)."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from ..config import DB_PATH, PHOTO_EXTS, RAW_EXTS, VIDEO_EXTS
from .sidecar import Sidecar, sidecar_path

_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with _lock, _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS folders(path TEXT PRIMARY KEY, added REAL);
            CREATE TABLE IF NOT EXISTS items(
                path TEXT PRIMARY KEY, folder TEXT, name TEXT, kind TEXT, ext TEXT,
                size INTEGER, mtime REAL, rating INTEGER DEFAULT 0, has_edits INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_items_folder ON items(folder);
            CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
            """
        )


def kind_of(p: Path) -> str | None:
    ext = p.suffix.lower()
    if ext in PHOTO_EXTS:
        return "photo"
    if ext in VIDEO_EXTS:
        return "video"
    return None


def scan_folder(folder: Path, recursive: bool = True) -> int:
    folder = folder.resolve()
    if not folder.is_dir():
        raise FileNotFoundError(folder)
    it = folder.rglob("*") if recursive else folder.glob("*")
    rows = []
    for p in it:
        if not p.is_file() or p.name.startswith("."):
            continue
        k = kind_of(p)
        if not k:
            continue
        st = p.stat()
        sc = sidecar_path(p)
        rating, has_edits = 0, 0
        if sc.exists():
            s = Sidecar(p)
            rating = s.rating
            has_edits = 0 if s.settings.is_identity() else 1
        rows.append((str(p), str(folder), p.name, k, p.suffix.lower(), st.st_size, st.st_mtime, rating, has_edits))
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO folders(path, added) VALUES(?, strftime('%s','now'))", (str(folder),))
        c.execute("DELETE FROM items WHERE folder=?", (str(folder),))
        c.executemany("INSERT OR REPLACE INTO items VALUES(?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def list_folders() -> list[str]:
    with _lock, _conn() as c:
        return [r["path"] for r in c.execute("SELECT path FROM folders ORDER BY added")]


def remove_folder(folder: str) -> None:
    with _lock, _conn() as c:
        c.execute("DELETE FROM items WHERE folder=?", (folder,))
        c.execute("DELETE FROM folders WHERE path=?", (folder,))


def list_items(folder: str | None = None, kind: str | None = None, min_rating: int = 0, sort: str = "name") -> list[dict]:
    q = "SELECT * FROM items WHERE 1=1"
    args: list = []
    if folder:
        q += " AND folder=?"
        args.append(folder)
    if kind in ("photo", "video"):
        q += " AND kind=?"
        args.append(kind)
    if min_rating:
        q += " AND rating>=?"
        args.append(min_rating)
    order = {"name": "name COLLATE NOCASE", "date": "mtime DESC", "rating": "rating DESC, name COLLATE NOCASE"}.get(sort, "name")
    q += f" ORDER BY {order}"
    with _lock, _conn() as c:
        return [dict(r) for r in c.execute(q, args)]


def get_item(path: str) -> dict | None:
    with _lock, _conn() as c:
        r = c.execute("SELECT * FROM items WHERE path=?", (path,)).fetchone()
        return dict(r) if r else None


def update_item(path: str, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with _lock, _conn() as c:
        c.execute(f"UPDATE items SET {cols} WHERE path=?", (*fields.values(), path))


def is_raw_item(path: str) -> bool:
    return Path(path).suffix.lower() in RAW_EXTS
