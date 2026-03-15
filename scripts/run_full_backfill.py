from __future__ import annotations
import subprocess
import sys
from typing import List, Tuple

from src.storage import meta_store
from src.common.logger import get_logger
from src.common.settings import settings

log = get_logger(__name__)

# 초기 구축용 백필 구간 (필요 시 수정 가능)
PHASES: List[Tuple[str, str]] = [
    ("2016-01-01", "2018-12-31"),
    ("2019-01-01", "2021-12-31"),
    ("2022-01-01", "2025-12-31"),
]


def phase_note(start: str, end: str) -> str:
    return f"{start}..{end}"


def is_phase_done(note: str) -> bool:
    meta_store.ensure_tables()
    con = meta_store.connect()
    try:
        row = con.execute(
            """
            SELECT status
            FROM run_log
            WHERE job_name = 'backfill_job' AND note = ?
            ORDER BY ended_at DESC
            LIMIT 1
            """,
            [note],
        ).fetchone()
    finally:
        con.close()
    if not row:
        return False
    status = row[0]
    return status == "success"


def run_phase(start: str, end: str) -> None:
    log.info("run backfill phase: %s..%s", start, end)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "src.jobs.backfill_job",
            "--start-date",
            start,
            "--end-date",
            end,
        ],
        check=True,
        cwd=str(settings.project_root),
    )


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Run full backfill in phases")
    parser.add_argument("--force", action="store_true", help="Re-run all phases (ignore run_log success)")
    args = parser.parse_args()
    for start, end in PHASES:
        note = phase_note(start, end)
        if not args.force and is_phase_done(note):
            log.info("skip phase (already success): %s", note)
            continue
        run_phase(start, end)


if __name__ == "__main__":
    main()

