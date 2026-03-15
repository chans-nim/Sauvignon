from __future__ import annotations
from datetime import datetime, timedelta
from pathlib import Path
import hashlib

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def chunk_date_ranges(start_date: str, end_date: str, days: int):
    start = datetime.fromisoformat(start_date)
    end = datetime.fromisoformat(end_date)
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=days-1), end)
        yield cur.date().isoformat(), chunk_end.date().isoformat()
        cur = chunk_end + timedelta(days=1)
