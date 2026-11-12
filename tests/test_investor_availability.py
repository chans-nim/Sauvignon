from datetime import datetime
from html import unescape
from zoneinfo import ZoneInfo

import pytest

from pathlib import Path

from scripts.collect_thema_sector_data import (
    _missing_investor_symbols, _require_investor_after_close,
    _investor_collection_enabled, _fetch_quote_enrichment_concurrent,
    _score_group_members, _render_theme_report_html, _render_theme_summary_md,
)
from sector_scanner.kis_client import KISClient


def test_strict_intraday_flow_fails_before_api_enrichment():
    with pytest.raises(RuntimeError, match="after market close"):
        _require_investor_after_close(datetime(2026, 10, 6, 12, 55, tzinfo=ZoneInfo("Asia/Seoul")))
    _require_investor_after_close(datetime(2026, 10, 6, 20, 30, tzinfo=ZoneInfo("Asia/Seoul")))


def test_official_institution_field_is_parsed_and_zero_is_valid(monkeypatch):
    client = KISClient()
    calls = []
    def get(path, params, tr_id):
        calls.append(path)
        return {"output": [{"frgn_ntby_tr_pbmn": "0", "orgn_ntby_tr_pbmn": "-500"}]}
    monkeypatch.setattr(client, "_get", get)
    result = client.fetch_foreign_institution_for_symbol("005930")
    assert result["foreign_net_tr_pbmn"] == 0
    assert result["institution_net_tr_pbmn"] == -500
    assert calls == ["/uapi/domestic-stock/v1/quotations/inquire-investor"]


def test_dated_investor_uses_only_requested_trading_date(monkeypatch):
    client = KISClient()
    def get(path, params, tr_id):
        assert path.endswith("investor-trade-by-stock-daily")
        assert params["FID_INPUT_DATE_1"] == "20261109"
        assert tr_id == "FHPTJ04160001"
        return {"output2": [
            {"stck_bsop_date": "20261110", "frgn_ntby_tr_pbmn": "999", "orgn_ntby_tr_pbmn": "999"},
            {"stck_bsop_date": "20261109", "frgn_ntby_tr_pbmn": "1200", "orgn_ntby_tr_pbmn": "-800"},
        ]}
    monkeypatch.setattr(client, "_get", get)
    result = client.fetch_foreign_institution_for_symbol("005930", trading_date="2026-11-09")
    assert result["trading_date"] == "2026-11-09"
    assert result["foreign_net_tr_pbmn"] == 1200
    assert result["institution_net_tr_pbmn"] == -800


def test_daily_quote_selects_exact_session_and_computes_return():
    class Auth:
        def get_daily_ohlcv(self, symbol, start, end, market_div_code="J"):
            return {"output2": [
                {"stck_bsop_date": "20261106", "stck_oprc": "90", "stck_hgpr": "101", "stck_lwpr": "89", "stck_clpr": "100", "acml_vol": "10", "acml_tr_pbmn": "950"},
                {"stck_bsop_date": "20261109", "stck_oprc": "101", "stck_hgpr": "112", "stck_lwpr": "100", "stck_clpr": "110", "acml_vol": "20", "acml_tr_pbmn": "2100"},
            ]}
    result = KISClient(auth=Auth()).fetch_stock_daily_quote("005930", "2026-11-09")
    assert result["close"] == 110
    assert result["previous_close"] == 100
    assert result["return_pct"] == pytest.approx(0.1)
    assert result["value_traded"] == 2100
    assert result["trading_date"] == "2026-11-09"


def test_dated_program_flow_uses_official_daily_endpoint(monkeypatch):
    client = KISClient()
    def get(path, params, tr_id):
        assert path.endswith("program-trade-by-stock-daily")
        assert params["FID_INPUT_DATE_1"] == "20261109"
        assert tr_id == "FHPPG04650201"
        return {"output": [
            {"stck_bsop_date": "20261109", "whol_smtn_ntby_tr_pbmn": "3456"},
        ]}
    monkeypatch.setattr(client, "_get", get)
    result = client.fetch_program_trade_net_for_symbol("005930", trading_date="2026-11-09")
    assert result == {
        "program_net_tr_pbmn": 3456,
        "symbol": "005930",
        "trading_date": "2026-11-09",
    }


def test_coverage_requires_both_foreign_and_institution_values():
    assert _missing_investor_symbols(["005930"], {"005930": {
        "foreign_net_tr_pbmn": 0, "institution_net_tr_pbmn": None}}) == ["005930"]


def test_confirmed_no_trade_is_exempt_without_fabricating_flow():
    investors = {}
    quotes = {"043220": {"no_trade": True}, "054180": {"no_trade": True}}
    assert _missing_investor_symbols(["043220", "054180"], investors, quotes) == []
    assert investors == {}
    assert _missing_investor_symbols(["043220", "054180"], investors) == ["043220", "054180"]


@pytest.mark.parametrize("quote", [{}, {"volume": 0, "value_traded": 0}, {"no_trade": False}])
def test_missing_or_traded_quotes_still_require_investor_values(quote):
    assert _missing_investor_symbols(["043220"], {}, {"043220": quote}) == ["043220"]


def test_auto_omits_intraday_investor_but_requires_after_close():
    intraday = datetime(2026, 10, 6, 12, 55, tzinfo=ZoneInfo("Asia/Seoul"))
    close = datetime(2026, 10, 6, 20, 30, tzinfo=ZoneInfo("Asia/Seoul"))
    assert not _investor_collection_enabled("auto", intraday)
    assert _investor_collection_enabled("auto", close)
    assert not _investor_collection_enabled("omit", close)
    # required can collect the latest completed session through the dated API.
    assert _investor_collection_enabled("required", intraday)


def test_omitted_flow_preserves_quote_program_and_missing_values():
    class Client:
        def fetch_stock_price(self, symbol):
            return {"price": 100, "return_pct": 0.02, "value_traded": 1000}
        def fetch_program_trade_net_for_symbol(self, symbol):
            return {"program_net_tr_pbmn": 500}
        def fetch_foreign_institution_for_symbol(self, symbol):
            raise AssertionError("omitted investor API must not be called")
    quotes, investors, programs = _fetch_quote_enrichment_concurrent(
        Client(), ["005930"], fetch_investor_per_symbol=False)
    assert investors == {}
    members = _score_group_members([{"symbol": "005930", "name": "Samsung"}], quotes, {},
                                   investor_by_symbol=investors, program_by_symbol=programs)
    assert members[0]["price"] == 100
    assert members[0]["program_net_tr_pbmn"] == 500
    assert members[0]["foreign_net_tr_pbmn"] is None
    assert members[0]["institution_net_tr_pbmn"] is None


def test_report_formats_explain_omitted_investor_collection():
    note = "외국인·기관 수급 제외: 미제공 값은 '-'로 표시합니다."
    kwargs = dict(source_path=Path("classification.json"), collected_at="2026-10-06T12:55:00",
                  meta={"investor_collection_note": note}, major_rows=[], middle_rows=[], top_n=5)
    assert note in unescape(_render_theme_report_html(**kwargs))
    assert note in _render_theme_summary_md(**kwargs)
