from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_daily_collect import run_incremental_and_gap_fill
from src.jobs.gap_fill_job import run_gap_fill


def test_run_gap_fill_logs_partial_when_some_collects_fail() -> None:
    # 검증: 일부 구간 수집 실패 시 gap_fill_job은 partial run_log 상태를 남긴다.
    gap_df = pd.DataFrame(
        [
            {"symbol": "005930", "market": "KOSPI", "name": "삼성전자", "start_date": "2026-03-10", "end_date": "2026-03-10"},
            {"symbol": "000660", "market": "KOSPI", "name": "SK하이닉스", "start_date": "2026-03-10", "end_date": "2026-03-10"},
        ]
    )
    collected: list[str] = []
    run_log_calls: list[tuple] = []

    def fake_gap_loader(**kwargs):
        return gap_df

    def fake_collector(symbol, market, name, start_date, end_date):
        collected.append(symbol)
        if symbol == "000660":
            return symbol, False, "temporary failure"
        return symbol, True, None

    def fake_log_run(*args, **kwargs):
        run_log_calls.append((args, kwargs))

    total, success, failed = run_gap_fill(
        target_start="2026-03-10",
        target_end="2026-03-10",
        gap_loader=fake_gap_loader,
        collector=fake_collector,
        ensure_tables_fn=lambda: None,
        log_run_fn=fake_log_run,
    )

    assert (total, success, failed) == (2, 1, 1)
    assert collected == ["005930", "000660"]
    assert run_log_calls[0][0][2] == "partial"


def test_run_incremental_and_gap_fill_calls_gap_fill_after_incremental() -> None:
    # 검증: 증분 수집이 끝나면 같은 날짜 범위로 gap fill이 이어서 실행된다.
    calls: list[tuple[str, str, str]] = []

    def fake_incremental(start: date, end: date) -> None:
        calls.append(("incremental", start.isoformat(), end.isoformat()))

    def fake_gap_fill_runner(**kwargs):
        calls.append(("gap_fill", kwargs["target_start"], kwargs["target_end"]))
        return 0, 0, 0

    run_incremental_and_gap_fill(
        date(2026, 3, 10),
        date(2026, 3, 12),
        incremental_runner=fake_incremental,
        gap_fill_runner=fake_gap_fill_runner,
    )

    assert calls == [
        ("incremental", "2026-03-10", "2026-03-12"),
        ("gap_fill", "2026-03-10", "2026-03-12"),
    ]
