from datetime import date

import pandas as pd
import pytest

from src.collect import collect_daily
from src.collect.status_recovery import classify_stock_status, persist_confirmed_status
from src.storage import meta_store

ROW = {"symbol": "005110", "std_code": "KR7005110002", "market": "KOSPI", "name": "Hanchang"}
MISSING = "KIS response has no valid row for required target date 2026-10-15; received_rows=0 target_rows=0 target_sample=[]"


def info(**fields):
    return {"rt_cd": "0", "output": {"std_pdno": ROW["std_code"], **fields}}


@pytest.mark.parametrize("field", ["scts_mket_lstg_abol_dt", "lstg_abol_dt"])
def test_delisting_requires_effective_date(field):
    payload = info(**{field: "20261015"})
    assert classify_stock_status(ROW, payload, "2026-10-14") is None
    assert classify_stock_status(ROW, payload, "2026-10-15") == "delisted"


@pytest.mark.parametrize("payload", [{}, {"output": []}, info(std_pdno="DIFFERENT"),
    info(lstg_abol_dt="20269999"), info(lstg_abol_dt="00000000"),
    info(lstg_abol_dt="20261015", scts_mket_lstg_dt="20261016")])
def test_missing_malformed_or_reused_listing_is_not_excluded(payload):
    assert classify_stock_status(ROW, payload, "2026-10-16") is None


def test_current_halt_cannot_explain_historical_missing_row():
    payload = info(tr_stop_yn="Y")
    assert classify_stock_status(ROW, payload, "2026-10-15", today=date(2026, 10, 16)) is None
    assert classify_stock_status(ROW, payload, "2026-10-16", today=date(2026, 10, 16)) == "trading_halt"


@pytest.mark.parametrize("error,recovered", [(MISSING, True), ("network failed", False),
    (MISSING.replace("target_rows=0", "target_rows=1"), False)])
def test_recovery_only_after_retries_and_only_for_absent_rows(monkeypatch, error, recovered):
    calls, persisted, report, delays = [], [], [], []
    monkeypatch.setattr(collect_daily, "_collect_row", lambda *args: (calls.append(args) or False, error))
    class Client:
        def get_stock_info(self, symbol):
            assert len(calls) == 3
            return info(lstg_abol_dt="20261015")
    monkeypatch.setattr(collect_daily, "_client", Client())
    monkeypatch.setattr(collect_daily, "persist_confirmed_status", lambda *args: persisted.append(args))
    assert collect_daily.collect_rows([ROW], "2026-10-15", "2026-10-15", report=report,
        sleep_fn=delays.append) == ((1, 0) if recovered else (0, 1))
    assert bool(persisted) == recovered
    assert delays == [2, 4]
    assert report[0]["outcome"] == ("delisted" if recovered else "failed")


def test_status_lookup_failure_remains_reported_failure(monkeypatch):
    monkeypatch.setattr(collect_daily, "_collect_row", lambda *args: (False, MISSING))
    monkeypatch.setattr(collect_daily, "_client", object())
    report = []
    assert collect_daily.collect_rows([ROW], "2026-10-15", "2026-10-15", max_attempts=1, report=report) == (0, 1)
    assert "recovery_error" in report[0]


def test_persisted_exclusion_updates_shared_scope_without_advancing_price_date(tmp_path, monkeypatch):
    monkeypatch.setattr(meta_store, "META_DB", tmp_path / "meta.duckdb")
    meta_store.ensure_tables()
    with meta_store.connect() as con:
        con.execute("INSERT INTO universe (symbol,std_code,name,market,is_active) VALUES (?,?,?,?,TRUE)",
                    [ROW[k] for k in ("symbol", "std_code", "name", "market")])
    meta_store.upsert_collect_state(ROW["symbol"], True, "2026-10-14", None)
    meta_store.upsert_collect_state(ROW["symbol"], False, None, MISSING)
    persist_confirmed_status(ROW, "delisted")
    assert meta_store.load_universe().empty
    with meta_store.connect() as con:
        assert con.execute("SELECT last_success_date,last_error FROM collect_state").fetchone() == (date(2026, 10, 14), None)


def test_daily_universe_excludes_future_listing(tmp_path, monkeypatch):
    monkeypatch.setattr(meta_store, "META_DB", tmp_path / "meta.duckdb")
    meta_store.ensure_tables()
    with meta_store.connect() as con:
        con.execute("INSERT INTO universe (symbol,is_active,listing_date) VALUES ('005930',TRUE,'2026-10-16')")
    assert meta_store.load_universe(target_date="2026-10-15").empty
    assert len(meta_store.load_universe(target_date="2026-10-16")) == 1


def test_stock_info_request_contract(monkeypatch):
    from src.clients.kis_client import KISClient
    client = KISClient()
    calls = []
    monkeypatch.setattr(client, "request_get", lambda *args: calls.append(args) or info())
    client.get_stock_info("005110")
    assert calls == [("/uapi/domestic-stock/v1/quotations/search-stock-info",
                      {"PRDT_TYPE_CD": "300", "PDNO": "005110"}, "CTPF1002R")]


def test_identity_change_before_update_rolls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(meta_store, "META_DB", tmp_path / "meta.duckdb")
    meta_store.ensure_tables()
    with meta_store.connect() as con:
        con.execute("INSERT INTO universe (symbol,std_code,market,is_active) VALUES ('005110','NEW','KOSPI',TRUE)")
    with pytest.raises(RuntimeError, match="exactly one listing"):
        persist_confirmed_status(ROW, "delisted")
    with meta_store.connect() as con:
        assert con.execute("SELECT is_active FROM universe").fetchone() == (True,)
