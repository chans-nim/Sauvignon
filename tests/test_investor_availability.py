from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from scripts.collect_thema_sector_data import _missing_investor_symbols, _require_investor_after_close
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


def test_coverage_requires_both_foreign_and_institution_values():
    assert _missing_investor_symbols(["005930"], {"005930": {
        "foreign_net_tr_pbmn": 0, "institution_net_tr_pbmn": None}}) == ["005930"]
