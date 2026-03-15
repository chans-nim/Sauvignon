from __future__ import annotations
from datetime import date, timedelta
import subprocess
import sys

from src.storage import meta_store
from src.common.logger import get_logger
from src.jobs.gap_fill_job import run_gap_fill

log = get_logger(__name__)


def get_last_success_date() -> date | None:
    """
    collect_state에서 1d 타임프레임의 마지막 성공일을 조회한다.
    """
    meta_store.ensure_tables()
    con = meta_store.connect()
    try:
        row = con.execute(
            """
            SELECT MAX(last_success_date)
            FROM collect_state
            WHERE timeframe = '1d'
            """,
        ).fetchone()
    finally:
        con.close()
    if not row or row[0] is None:
        return None
    return row[0]


def run_incremental(start: date, end: date) -> None:
    log.info("run incremental collect: %s..%s", start.isoformat(), end.isoformat())
    subprocess.run(
        [
            sys.executable,
            "-m",
            "src.jobs.incremental_job",
            "--start-date",
            start.isoformat(),
            "--end-date",
            end.isoformat(),
        ],
        check=True,
    )


def run_incremental_and_gap_fill(
    start: date,
    end: date,
    *,
    incremental_runner=run_incremental,
    gap_fill_runner=run_gap_fill,
) -> None:
    incremental_runner(start, end)
    log.info("run post-incremental gap fill: %s..%s", start.isoformat(), end.isoformat())
    gap_fill_runner(target_start=start.isoformat(), target_end=end.isoformat(), merge=True)


def main() -> None:
    today = date.today()
    target_end = today - timedelta(days=1)
    if target_end < date(2000, 1, 1):
        # 비정상적인 시스템 시간 보호
        log.info("system date looks wrong, skip collect")
        return

    last_success = get_last_success_date()
    if last_success is None:
        log.info("no existing collect_state; nothing to do in daily mode")
        return

    start = last_success + timedelta(days=1)
    if start > target_end:
        log.info("no new trading days to collect (%s > %s)", start, target_end)
        return

    run_incremental_and_gap_fill(start, target_end)


if __name__ == "__main__":
    main()

