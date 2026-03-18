from __future__ import annotations
from datetime import date, datetime, timedelta
import subprocess
import sys

import duckdb

from src.storage import meta_store
from src.common.logger import get_logger
from src.jobs.gap_fill_job import run_gap_fill
from src.common.settings import settings

log = get_logger(__name__)


def check_last_collect_failure() -> None:
    """
    직전 collect_daily run_log에서 전량 실패(total > 0 and success == 0)면 워크플로 실패를 위해 exit(1).
    KIS 키 오류/한도 등으로 수집이 하나도 안 된 상태에서 스냅샷을 덮어쓰지 않도록 한다.
    """
    meta_store.ensure_tables()
    con = meta_store.connect()
    try:
        row = con.execute(
            """
            SELECT total_symbols, success_symbols, failed_symbols
            FROM run_log
            WHERE job_name = 'collect_daily'
            ORDER BY started_at DESC
            LIMIT 1
            """
        ).fetchone()
    finally:
        con.close()
    if not row:
        return
    total, success, failed = int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)
    if total > 0 and success == 0:
        log.error("collect_daily: all %s symbols failed; exiting so workflow does not publish stale snapshot", total)
        sys.exit(1)


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
    # duckdb may return datetime/date depending on parquet type.
    # NOTE: datetime is a subclass of date, so handle it first.
    if isinstance(v, datetime):
        return v.date()
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
    # 당일까지 수집(target_end = 오늘). 로컬 TZ 사용, Actions에서는 TZ=Asia/Seoul(KST) 적용.
    # 오늘은 가격 변동이 있을 수 있으므로, 이미 당일 데이터가 있어도 매 run에서 갱신한다.
    today = date.today()
    target_end = today
    if target_end < date(2000, 1, 1):
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
        # 이미 당일까지 있음 → 오늘만 다시 수집해 최신 가격으로 갱신
        start = today
        end = today
        log.info("refreshing today only: %s", end.isoformat())
    else:
        end = target_end

    run_incremental_and_gap_fill(start, end)
    check_last_collect_failure()


if __name__ == "__main__":
    main()

