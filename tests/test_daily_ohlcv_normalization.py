import pandas as pd
import pytest

from src.collect.base_collect import normalize_ohlcv, validate_ohlcv


def payload(**changes):
    row = dict(stck_bsop_date="20261007", stck_oprc="0", stck_hgpr="0",
               stck_lwpr="0", stck_clpr="10000", acml_vol="0", acml_tr_pbmn="0")
    row.update(changes)
    return {"output2": [row]}


@pytest.mark.parametrize("zero", [0, "0"])
def test_explicit_no_trade_row_uses_its_own_close(zero):
    df = normalize_ohlcv("196490", "KOSPI", payload(acml_vol=zero, acml_tr_pbmn=zero))
    valid = validate_ohlcv(df)
    assert len(valid) == 1
    assert valid.iloc[0]["date"] == pd.Timestamp("2026-10-07")
    assert valid.iloc[0][["open", "high", "low", "close"]].tolist() == [10000] * 4
    assert valid.iloc[0][["volume", "value"]].tolist() == [0, 0]


@pytest.mark.parametrize("changes", [
    {"acml_vol": "1"}, {"acml_vol": None}, {"acml_tr_pbmn": "1"},
    {"stck_oprc": ""}, {"stck_clpr": "0"}, {"stck_hgpr": "9000"},
])
def test_missing_or_inconsistent_row_is_not_repaired(changes):
    assert validate_ohlcv(normalize_ohlcv("196490", "KOSPI", payload(**changes))).empty


def test_undated_placeholder_produces_empty_frame():
    assert normalize_ohlcv("196490", "KOSPI", {"output2": [{}]}).empty


def test_numeric_zero_volume_is_preserved_for_normal_prices():
    df = normalize_ohlcv("196490", "KOSPI", payload(
        stck_oprc=10000, stck_hgpr=10000, stck_lwpr=10000, acml_vol=0, acml_tr_pbmn=0))
    assert len(validate_ohlcv(df)) == 1


@pytest.mark.parametrize("reference", [93805, 93815])
def test_flat_no_trade_reference_prices_use_reported_close(reference):
    df = normalize_ohlcv("498180", "KOSPI", payload(
        stck_bsop_date="20261008", stck_oprc=reference, stck_hgpr=reference,
        stck_lwpr=reference, stck_clpr=93810))
    valid = validate_ohlcv(df)
    assert len(valid) == 1
    assert valid.iloc[0]["date"] == pd.Timestamp("2026-10-08")
    assert valid.iloc[0][["open", "high", "low", "close"]].tolist() == [93810] * 4
    assert valid.iloc[0][["volume", "value"]].tolist() == [0, 0]


@pytest.mark.parametrize("changes", [
    {"acml_vol": 1}, {"acml_tr_pbmn": 1},
    {"acml_vol": None}, {"acml_tr_pbmn": None},
    {"stck_oprc": None}, {"stck_lwpr": 93800},
    {"stck_oprc": -1, "stck_hgpr": -1, "stck_lwpr": -1},
])
def test_inconsistent_flat_reference_row_is_not_repaired(changes):
    fields = dict(stck_oprc=93805, stck_hgpr=93805, stck_lwpr=93805,
                  stck_clpr=93810)
    fields.update(changes)
    assert validate_ohlcv(normalize_ohlcv("498180", "KOSPI", payload(**fields))).empty
