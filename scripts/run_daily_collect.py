from __future__ import annotations
from datetime import date, datetime, time, timedelta
import os
import subprocess
import sys
from zoneinfo import ZoneInfo

import duckdb

from src.storage import meta_store
from src.common.logger import get_logger
from src.jobs.gap_fill_job import run_gap_fill
from src.common.settings import settings
from src.clients.kis_auth import get_client

log = get_logger(__name__)
KST = ZoneInfo("Asia/Seoul")


def resolve_collection_target_date(now: datetime | None = None, *, calendar_loader=None) -> date:
    """Resolve the market date independently of delayed GitHub scheduling.

    Before the Korean market opens, collect the previous weekday instead of treating the
    new calendar day as the target. Production uses the KIS exchange calendar;
    missing calendar data fails closed instead of guessing from empty price responses.
    """
    current = now.astimezone(KST) if now is not None and now.tzinfo else (now.replace(tzinfo=KST) if now else datetime.now(KST))
    candidate = current.date()
    if current.time() < time(9, 0):
        candidate -= timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    if calendar_loader is not None:
        start = candidate - timedelta(days=14)
        rows = calendar_loader(start.isoformat())
        calendar = {}
        for row in rows:
            raw_date = str(row.get("bass_dt") or "").strip()
            if not raw_date:
                continue
            day = datetime.strptime(raw_date, "%Y%m%d").date()
            flag = str(row.get("opnd_yn") or "").strip().upper()
            if flag not in {"Y", "N"}:
                raise RuntimeError(f"KIS calendar has invalid opening flag for {day}")
            calendar[day] = flag == "Y"
        while candidate >= start:
            if candidate not in calendar:
                raise RuntimeError(f"KIS calendar has no opening information for {candidate}")
            if calendar[candidate]:
                return candidate
            candidate -= timedelta(days=1)
        raise RuntimeError("KIS calendar has no open trading date in the last 14 days")
    return candidate


def write_github_output(name: str, value: object) -> None:
    path = (os.getenv("GITHUB_OUTPUT") or "").strip()
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{name}={value}\n")


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
) -> tuple[int, int, int]:
    incremental_runner(start, end)
    log.info("run post-incremental gap fill: %s..%s", start.isoformat(), end.isoformat())
    total, success, failed = gap_fill_runner(target_start=start.isoformat(), target_end=end.isoformat(), merge=True)
    if failed:
        raise RuntimeError(
            f"gap fill incomplete: total={total} success={success} failed={failed}; refusing downstream publish"
        )
    return int(total), int(success), int(failed)


def main() -> None:
    # GitHub schedule이 자정을 넘어 지연돼도 새벽의 빈 날짜를 수집 대상으로 잡지 않는다.
    target_end = resolve_collection_target_date(calendar_loader=get_client().get_market_calendar)
    write_github_output("target_date", target_end.isoformat())
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
        # 이미 대상일까지 있음 → 대상일을 강제로 다시 받아 장중 값을 마감 값으로 갱신한다.
        start = target_end
        end = target_end
        log.info("refreshing today only: %s", end.isoformat())
    else:
        end = target_end

    write_github_output("collection_start_date", start.isoformat())
    gap_total, gap_success, gap_failed = run_incremental_and_gap_fill(start, end)
    check_last_collect_failure()
    write_github_output("gap_total", gap_total)
    write_github_output("gap_success", gap_success)
    write_github_output("gap_failed", gap_failed)


if __name__ == "__main__":
    main()

