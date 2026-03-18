from __future__ import annotations
from datetime import date, timedelta
import subprocess
import sys

import duckdb

from src.storage import meta_store
from src.common.logger import get_logger
from src.jobs.gap_fill_job import run_gap_fill
from src.common.settings import settings

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


def get_last_silver_date() -> date | None:
    """
    Silver parquet 전체에서 MAX(date)를 조회한다.
    collect_state가 비어 있는(예: Actions에서 base snapshot을 먼저 주입한) 경우에 사용한다.
    """
    silver_dir = settings.project_root / "data" / "lake" / "silver" / "ohlcv_daily"
    if not silver_dir.exists():
        return None
    paths = [p.as_posix() for p in silver_dir.rglob("data.parquet")]
    if not paths:
        return None
    con = duckdb.connect()
    try:
        row = con.execute("SELECT MAX(date) FROM read_parquet(?)", [paths]).fetchone()
    finally:
        con.close()
    if not row or row[0] is None:
        return None
    v = row[0]
    # duckdb may return datetime/date depending on parquet type
    if hasattr(v, "date") and not isinstance(v, date):
        try:
            return v.date()
        except Exception:
            pass
    if isinstance(v, date):
        return v
    # last resort: parse ISO-like string
    try:
        return date.fromisoformat(str(v)[:10])
    except Exception:
        return None


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
        last_silver = get_last_silver_date()
        if last_silver is None:
            log.info("no existing collect_state and silver is empty; nothing to do")
            return
        log.info("collect_state empty; using last silver date=%s as baseline", last_silver.isoformat())
        last_success = last_silver

    start = last_success + timedelta(days=1)
    if start > target_end:
        log.info("no new trading days to collect (%s > %s)", start, target_end)
        return

    run_incremental_and_gap_fill(start, target_end)


if __name__ == "__main__":
    main()

