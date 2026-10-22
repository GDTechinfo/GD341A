"""Histórico de serviços (SQLite local): o que foi lido/gravado, em qual chip, com qual hash."""

import os
import sqlite3
import time
from contextlib import contextmanager

from .chipdb import config_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    op TEXT NOT NULL,
    chip TEXT, chip_id TEXT, size INTEGER,
    crc32 TEXT, sha256 TEXT,
    file TEXT, result TEXT,
    job TEXT, note TEXT
);
CREATE INDEX IF NOT EXISTS jobs_ts ON jobs(ts);
"""


class History:
    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(config_dir(), "historico.sqlite")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with self._db() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def add(self, op: str, chip: str = "", chip_id: str = "", size: int = 0, crc32: str = "",
            sha256: str = "", file: str = "", result: str = "", job: str = "", note: str = "") -> int:
        with self._db() as db:
            cur = db.execute(
                "INSERT INTO jobs(ts, op, chip, chip_id, size, crc32, sha256, file, result, job, note)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (time.time(), op, chip, chip_id, size, crc32, sha256, file, result, job, note))
            return cur.lastrowid

    def set_file(self, row_id: int, file: str):
        with self._db() as db:
            db.execute("UPDATE jobs SET file=? WHERE id=?", (file, row_id))

    def set_note(self, row_id: int, note: str):
        with self._db() as db:
            db.execute("UPDATE jobs SET note=? WHERE id=?", (note, row_id))

    def search(self, text: str = "", limit: int = 500) -> list[dict]:
        q = "SELECT id, ts, op, chip, chip_id, size, crc32, sha256, file, result, job, note FROM jobs"
        args = []
        if text:
            like = f"%{text}%"
            q += (" WHERE chip LIKE ? OR chip_id LIKE ? OR crc32 LIKE ? OR sha256 LIKE ?"
                  " OR file LIKE ? OR job LIKE ? OR note LIKE ? OR op LIKE ?")
            args = [like] * 8
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        with self._db() as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute(q, args)]

    def find_by_hash(self, sha: str) -> list[dict]:
        with self._db() as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute(
                "SELECT * FROM jobs WHERE sha256=? ORDER BY ts DESC", (sha,))]
