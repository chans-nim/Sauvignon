from datetime import date, datetime
from types import SimpleNamespace

import pandas as pd

from scripts import audit_repair_last_week as audit
from scripts.run_daily_collect import resolve_collection_target_date


def test_audit_repair_uses_exchange_calendar_before_incremental(monkeypatch):
    rows = [
        {"bass_dt": "20261002", "opnd_yn": "Y"},
        {"bass_dt": "20261003", "opnd_yn": "N"},
        {"bass_dt": "20261004", "opnd_yn": "N"},
        {"bass_dt": "20261005", "opnd_yn": "N"},
    ]
    calls = []
    def calendar(start):
        calls.append(start)
        return rows
    monkeypatch.setattr(audit, "get_client", lambda: SimpleNamespace(get_market_calendar=calendar))
    monkeypatch.setattr(audit, "resolve_collection_target_date", lambda **kwargs:
                        resolve_collection_target_date(datetime(2026, 10, 6, 8), **kwargs))
    assert audit.resolve_audit_end_date(None) == date(2026, 10, 2)
    assert len(calls) == 1


def test_read_only_and_explicit_audit_do_not_call_kis(monkeypatch):
    def forbidden():
        raise AssertionError("unexpected KIS call")
    monkeypatch.setattr(audit, "get_client", forbidden)
    monkeypatch.setattr(audit, "resolve_collection_target_date", lambda: date(2026, 10, 5))
    assert audit.resolve_audit_end_date(None, read_only=True) == date(2026, 10, 5)
    assert audit.resolve_audit_end_date("2026-10-02") == date(2026, 10, 2)


def test_silver_audit_empty_day_returns_zero_counts(tmp_path):
    path = tmp_path / "daily.parquet"
    pd.DataFrame([{"symbol": "005930", "date": pd.Timestamp("2026-09-29"),
                   "volume": 10, "close": 100}]).to_parquet(path, index=False)
    stats = audit.audit_silver_date([str(path)], "2026-10-05")
    assert stats == {"date": "2026-10-05", "rows_on_date": 0, "volume_zero": 0,
                     "volume_zero_close_positive": 0, "distinct_symbols": 0}
