"""Tests for KIS quote/sector return_pct normalization (PRDY_CTRT is percent, not decimal)."""

from sector_scanner.kis_client import _norm_sector_current_row, _norm_stock_quote


def test_norm_stock_quote_small_positive_pct_is_decimal_not_hundredth():
    # 0.15% from API → 0.0015 decimal (old bug: kept 0.15 as if it were 15%)
    out = _norm_stock_quote({"prdy_ctrt": "0.15", "stck_prpr": "1000", "mksc_shrn_iscd": "099320"})
    assert out["return_pct"] == 0.0015


def test_norm_stock_quote_typical_pct():
    out = _norm_stock_quote({"PRDY_CTRT": "-1.25", "STCK_PRPR": "50000", "MKSC_SHRN_ISCD": "005930"})
    assert out["return_pct"] == -0.0125


def test_norm_sector_current_row_uses_percent_scale():
    row = {
        "BSTP_LCLSCD": "123",
        "HTS_KOR_ISNM": "Test",
        "BSTP_NMIX_PRPR": "100.0",
        "PRDY_CTRT": "0.32",
    }
    out = _norm_sector_current_row(row)
    assert out is not None
    assert out["return_pct"] == 0.0032
