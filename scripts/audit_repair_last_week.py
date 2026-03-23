"""
Silver 기준 최근 N일(기본 7일) 일자별 점검 후, 선택적으로 증분 수집 + volume=0(종가>0) 보정을 수행한다.

점검:
  - 일자별 행 수, 거래량 0 건수, 거래량 0 & 종가>0(의심) 건수, 종목 수

보정:
  - (기본) 해당 구간 전체 incremental_job (KIS 재수집·병합)
  - (기본) 구간 내 각 일자에 대해 repair_zero_volume_day와 동일한 후보 재수집

GitHub Actions: `.github/workflows/audit-repair-last-week.yml`

  python -m scripts.audit_repair_last_week
  python -m scripts.audit_repair_last_week --days 7 --dry-run
  python -m scripts.audit_repair_last_week --audit-only
  python -m scripts.audit_repair_last_week --skip-incremental --skip-zero-repair
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

if __name__ == "__main__" and str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common.logger import get_logger
from src.storage import meta_store
from src.transform.build_snapshot import silver_parquet_paths

import importlib.util

log = get_logger(__name__)


def _load_repair_helpers():
    """scripts 패키지 __init__ 없이도 동작하도록 로드."""
    path = Path(__file__).resolve().parent / "repair_zero_volume_day.py"
    spec = importlib.util.spec_from_file_location("repair_zero_volume_day", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load repair_zero_volume_day.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_rv = _load_repair_helpers()
audit_silver_date = _rv.audit_silver_date
load_zero_volume_candidates = _rv.load_zero_volume_candidates
load_low_volume_candidates = _rv.load_low_volume_candidates
run_repair_loop = _rv.run_repair_loop


def _daterange_inclusive(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit and optionally repair last N calendar days in silver")
    parser.add_argument("--days", type=int, default=7, help="Number of calendar days ending at --end-date (inclusive)")
    parser.add_argument("--end-date", default=None, help="YYYY-MM-DD (default: today, local TZ)")
    parser.add_argument("--dry-run", action="store_true", help="Print plan only; no KIS calls")
    parser.add_argument("--audit-only", action="store_true", help="Only print audit table; no repair")
    parser.add_argument("--skip-incremental", action="store_true", help="Do not run incremental_job for the range")
    parser.add_argument("--skip-zero-repair", action="store_true", help="Do not run per-day zero-volume repair")
    parser.add_argument(
        "--include-zero-close",
        action="store_true",
        help="Zero-volume repair includes close<=0 rows (same as repair_zero_volume_day --include-zero-close)",
    )
    parser.add_argument(
        "--limit-per-day",
        type=int,
        default=None,
        help="Max symbols to repair per day (testing)",
    )
    parser.add_argument(
        "--low-volume-ratio",
        type=float,
        default=0.1,
        help="Repair low-volume outliers when today_volume <= baseline_median * ratio (default: 0.1)",
    )
    parser.add_argument(
        "--low-volume-lookback-days",
        type=int,
        default=30,
        help="Lookback calendar days for baseline median volume (default: 30)",
    )
    parser.add_argument(
        "--min-baseline-volume",
        type=int,
        default=1000,
        help="Skip low-volume outlier check when baseline median volume is below this (default: 1000)",
    )
    parser.add_argument(
        "--min-history-points",
        type=int,
        default=10,
        help="Minimum non-zero history rows for low-volume baseline (default: 10)",
    )
    parser.add_argument(
        "--skip-low-volume-repair",
        action="store_true",
        help="Do not run per-day low-volume outlier repair",
    )
    parser.add_argument("--json-summary", action="store_true", help="Print one-line JSON summary at end")
    args = parser.parse_args()

    if args.days < 1:
        parser.error("--days must be >= 1")

    end = date.fromisoformat(args.end_date) if args.end_date else date.today()
    start = end - timedelta(days=args.days - 1)

    paths = silver_parquet_paths()
    if not paths:
        log.error("No silver data (read_parquet paths empty). Sync snapshot or run collect first.")
        sys.exit(1)

    audit_rows: list[dict] = []
    for d in _daterange_inclusive(start, end):
        ds = d.isoformat()
        try:
            st = audit_silver_date(paths, ds)
        except Exception as e:
            st = {"date": ds, "rows_on_date": 0, "volume_zero": 0, "volume_zero_close_positive": 0, "distinct_symbols": 0, "error": str(e)}
        audit_rows.append(st)

    print("=" * 72)
    print(f"Silver audit: {start.isoformat()} .. {end.isoformat()} ({args.days} day(s))")
    print("=" * 72)
    for st in audit_rows:
        if st.get("error"):
            print(f"  {st['date']}: ERROR {st['error']}")
        else:
            print(
                f"  {st['date']}: rows={st['rows_on_date']:,} symbols={st['distinct_symbols']:,} "
                f"vol0={st['volume_zero']:,} vol0&close>0={st['volume_zero_close_positive']:,}"
            )
    print("=" * 72)

    total_suspicious = sum(int(st.get("volume_zero_close_positive") or 0) for st in audit_rows)
    low_candidates_before = 0
    if not args.skip_low_volume_repair:
        only_pos_close = not args.include_zero_close
        for st in audit_rows:
            ds = st.get("date")
            if not ds or st.get("rows_on_date", 0) == 0:
                continue
            try:
                low_df = load_low_volume_candidates(
                    paths,
                    ds,
                    ratio_threshold=args.low_volume_ratio,
                    lookback_days=args.low_volume_lookback_days,
                    min_baseline_volume=args.min_baseline_volume,
                    min_history_points=args.min_history_points,
                    only_positive_close=only_pos_close,
                )
            except FileNotFoundError as e:
                log.error("%s", e)
                sys.exit(2)
            low_candidates_before += int(len(low_df))
    if not args.skip_low_volume_repair:
        print(
            "low-volume candidates (before repair): "
            f"{low_candidates_before:,} "
            f"(ratio<={args.low_volume_ratio}, lookback={args.low_volume_lookback_days}, "
            f"min_baseline={args.min_baseline_volume}, min_history={args.min_history_points})"
        )
    if args.audit_only or args.dry_run:
        if args.json_summary:
            print(
                json.dumps(
                    {
                        "start": start.isoformat(),
                        "end": end.isoformat(),
                        "total_vol0_close_pos": total_suspicious,
                        "low_volume_candidates_before": low_candidates_before,
                    },
                    ensure_ascii=False,
                )
            )
        return

    meta_store.ensure_tables()
    inc_ok = True
    if not args.skip_incremental:
        log.info("Running incremental_job %s..%s", start.isoformat(), end.isoformat())
        r = subprocess_run_incremental(start.isoformat(), end.isoformat())
        if r != 0:
            log.error("incremental_job exited with %s; aborting before zero-volume repair", r)
            meta_store.log_run(
                f"audit_repair_week_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                "audit_repair_last_week",
                "partial",
                1,
                0,
                1,
                note=f"{start}..{end} incremental_failed rc={r}",
            )
            sys.exit(r)
        inc_ok = True

    total_ok = total_fail = total_proc = 0
    low_total_ok = low_total_fail = low_total_proc = 0
    if not args.skip_zero_repair:
        only_pos_close = not args.include_zero_close
        for st in audit_rows:
            ds = st.get("date")
            if not ds or st.get("rows_on_date", 0) == 0:
                continue
            try:
                df = load_zero_volume_candidates(paths, ds, only_positive_close=only_pos_close)
            except FileNotFoundError as e:
                log.error("%s", e)
                sys.exit(2)
            if df.empty:
                continue
            log.info("Zero-volume repair: date=%s candidates=%s", ds, len(df))
            rows = df.to_dict(orient="records")
            proc, ok, fail = run_repair_loop(ds, rows, limit=args.limit_per_day)
            total_proc += proc
            total_ok += ok
            total_fail += fail
    if not args.skip_low_volume_repair:
        only_pos_close = not args.include_zero_close
        for st in audit_rows:
            ds = st.get("date")
            if not ds or st.get("rows_on_date", 0) == 0:
                continue
            try:
                df = load_low_volume_candidates(
                    paths,
                    ds,
                    ratio_threshold=args.low_volume_ratio,
                    lookback_days=args.low_volume_lookback_days,
                    min_baseline_volume=args.min_baseline_volume,
                    min_history_points=args.min_history_points,
                    only_positive_close=only_pos_close,
                )
            except FileNotFoundError as e:
                log.error("%s", e)
                sys.exit(2)
            if df.empty:
                continue
            log.info(
                "Low-volume outlier repair: date=%s candidates=%s ratio<=%.4f lookback=%s min_baseline=%s min_history=%s",
                ds,
                len(df),
                args.low_volume_ratio,
                args.low_volume_lookback_days,
                args.min_baseline_volume,
                args.min_history_points,
            )
            rows = df[["symbol", "market", "name"]].to_dict(orient="records")
            proc, ok, fail = run_repair_loop(ds, rows, limit=args.limit_per_day)
            low_total_proc += proc
            low_total_ok += ok
            low_total_fail += fail

    run_id = f"audit_repair_week_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    note = (
        f"{start}..{end} incremental={not args.skip_incremental} "
        f"zero_repair={not args.skip_zero_repair} zproc={total_proc} zok={total_ok} zfail={total_fail} "
        f"low_repair={not args.skip_low_volume_repair} lproc={low_total_proc} lok={low_total_ok} lfail={low_total_fail}"
    )
    merged_proc = total_proc + low_total_proc
    merged_ok = total_ok + low_total_ok
    merged_fail = total_fail + low_total_fail
    if merged_proc > 0:
        tot, succ, fail = merged_proc, merged_ok, merged_fail
    elif not args.skip_incremental or not args.skip_zero_repair or not args.skip_low_volume_repair:
        tot, succ, fail = 1, 1, 0
    else:
        tot, succ, fail = 0, 0, 0
    meta_store.log_run(
        run_id,
        "audit_repair_last_week",
        "success" if fail == 0 and (tot == 0 or succ > 0) else "partial",
        tot,
        succ,
        fail,
        note=note,
    )
    if not args.skip_zero_repair:
        log.info("audit_repair_last_week zero-repair totals: processed=%s ok=%s fail=%s", total_proc, total_ok, total_fail)
    if not args.skip_low_volume_repair:
        log.info(
            "audit_repair_last_week low-volume totals: processed=%s ok=%s fail=%s",
            low_total_proc,
            low_total_ok,
            low_total_fail,
        )

    if args.json_summary:
        print(
            json.dumps(
                {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "total_vol0_close_pos_before": total_suspicious,
                },
                ensure_ascii=False,
            )
        )


def subprocess_run_incremental(start: str, end: str) -> int:
    import subprocess

    p = subprocess.run(
        [sys.executable, "-m", "src.jobs.incremental_job", "--start-date", start, "--end-date", end],
        check=False,
    )
    return int(p.returncode)


if __name__ == "__main__":
    main()
