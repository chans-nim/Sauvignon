from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
import sys
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from scripts import validate_snapshot
from scripts.prepare_snapshot_rerelease import prepare_bundle
from scripts.run_daily_collect import resolve_collection_target_date, run_incremental_and_gap_fill
from src.collect import collect_daily
from src.collect.base_collect import fetch_ohlcv_chunked
from src.master.universe_policy import (
    UNSUPPORTED_SYMBOL_ASSET_TYPE,
    classify_daily_asset_type,
    collectible_universe_sql,
    is_kis_daily_collectible_symbol,
)


class _DailyClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    def get_daily_ohlcv(self, **_kwargs):
        self.calls += 1
        return self.payload


def _existing_row(close: int = 100) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "005930",
                "market": "KOSPI",
                "date": pd.Timestamp("2026-09-28"),
                "open": 100,
                "high": 110,
                "low": 90,
                "close": close,
                "volume": 10,
                "value": 1000,
                "ingested_at": pd.Timestamp("2026-09-28T10:00:00"),
            }
        ]
    )


def test_force_fetch_end_date_does_not_reuse_intraday_row() -> None:
    client = _DailyClient(
        {
            "output2": [
                {
                    "stck_bsop_date": "20260928",
                    "stck_oprc": "100",
                    "stck_hgpr": "120",
                    "stck_lwpr": "90",
                    "stck_clpr": "115",
                    "acml_vol": "20",
                    "acml_tr_pbmn": "2300",
                }
            ]
        }
    )

    refreshed, _raw, skipped = fetch_ohlcv_chunked(
        client,
        "005930",
        "KOSPI",
        "2026-09-28",
        "2026-09-28",
        existing_df=_existing_row(),
        force_fetch_end_date=True,
    )

    assert client.calls == 1
    assert skipped == []
    assert int(refreshed.iloc[0]["close"]) == 115


def test_empty_required_date_never_upserts_or_erases_existing(monkeypatch) -> None:
    client = _DailyClient({"output2": []})
    upserts: list[pd.DataFrame] = []
    states: list[tuple] = []
    monkeypatch.setattr(collect_daily, "_client", client)
    monkeypatch.setattr(collect_daily.parquet_store, "load_symbol_range", lambda *_args: _existing_row())
    monkeypatch.setattr(collect_daily.parquet_store, "save_raw_json", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(collect_daily.parquet_store, "upsert_ohlcv_from_df", lambda df: upserts.append(df))
    monkeypatch.setattr(collect_daily.meta_store, "upsert_collect_state", lambda *args: states.append(args))

    ok, error = collect_daily._collect_row(
        {"symbol": "005930", "market": "KOSPI", "name": "Samsung"},
        "2026-09-28",
        "2026-09-28",
    )

    assert ok is False
    assert "no valid row" in str(error)
    assert upserts == []
    assert states[-1][1] is False


def test_collect_rows_retries_only_failed_symbols(monkeypatch) -> None:
    attempts: dict[str, int] = {}

    def fake_collect(row, _start, _end):
        symbol = str(row["symbol"])
        attempts[symbol] = attempts.get(symbol, 0) + 1
        if symbol == "A" and attempts[symbol] == 1:
            return False, "temporary"
        return True, None

    monkeypatch.setattr(collect_daily, "_collect_row", fake_collect)
    success, failed = collect_daily.collect_rows(
        [
            {"symbol": "A", "market": "KOSPI", "name": "A"},
            {"symbol": "B", "market": "KOSPI", "name": "B"},
        ],
        "2026-09-28",
        "2026-09-28",
        max_attempts=2,
    )

    assert (success, failed) == (2, 0)
    assert attempts == {"A": 2, "B": 1}


def test_delayed_early_morning_run_targets_previous_weekday() -> None:
    kst = ZoneInfo("Asia/Seoul")
    assert resolve_collection_target_date(datetime(2026, 9, 29, 3, 30, tzinfo=kst)) == date(2026, 9, 28)
    assert resolve_collection_target_date(datetime(2026, 9, 28, 8, 0, tzinfo=kst)) == date(2026, 9, 25)
    assert resolve_collection_target_date(datetime(2026, 9, 28, 16, 30, tzinfo=kst)) == date(2026, 9, 28)


def test_kis_daily_scope_keeps_normal_symbols_and_rejects_master_product_codes() -> None:
    assert is_kis_daily_collectible_symbol("005930") is True
    assert is_kis_daily_collectible_symbol(" 005930 ") is True
    assert is_kis_daily_collectible_symbol("Q76012348") is False
    assert is_kis_daily_collectible_symbol("12345") is False
    assert classify_daily_asset_type("Q76012348") == UNSUPPORTED_SYMBOL_ASSET_TYPE


def test_collectible_universe_sql_uses_same_scope_as_python_policy() -> None:
    import duckdb

    con = duckdb.connect()
    try:
        con.execute("CREATE TABLE universe(symbol TEXT, is_active BOOLEAN)")
        con.executemany(
            "INSERT INTO universe VALUES (?, ?)",
            [("005930", True), ("Q76012348", True), ("000660", False)],
        )
        actual = con.execute(
            f"SELECT symbol FROM universe u WHERE {collectible_universe_sql('u')} ORDER BY symbol"
        ).fetchall()
    finally:
        con.close()

    assert actual == [("005930",)]


def test_gap_fill_failure_blocks_downstream_publish() -> None:
    with pytest.raises(RuntimeError, match="gap fill incomplete"):
        run_incremental_and_gap_fill(
            date(2026, 9, 28),
            date(2026, 9, 28),
            incremental_runner=lambda *_args: None,
            gap_fill_runner=lambda **_kwargs: (2, 1, 1),
        )


def test_strict_file_validation_blocks_duplicate_release_rows(tmp_path, monkeypatch) -> None:
    path = tmp_path / "duplicate.parquet"
    row = {
        "symbol": "005930",
        "market": "KOSPI",
        "date": pd.Timestamp("2026-09-28"),
        "open": 100,
        "high": 110,
        "low": 90,
        "close": 105,
        "volume": 10,
        "value": 1000,
        "ingested_at": pd.Timestamp("2026-09-28T16:30:00"),
    }
    pd.DataFrame([row, row]).to_parquet(path, index=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_snapshot",
            "--file-path",
            str(path),
            "--strict",
            "--file-only",
            "--expected-row-count",
            "2",
            "--expected-max-date",
            "2026-09-28",
        ],
    )

    with pytest.raises(SystemExit, match="duplicate symbol/date keys=1"):
        validate_snapshot.main()


def _write_validation_meta(path, symbols: list[str]) -> None:
    import duckdb

    con = duckdb.connect(str(path))
    try:
        con.execute(
            """
            CREATE TABLE universe (
                symbol TEXT,
                name TEXT,
                market TEXT,
                asset_type TEXT,
                listing_date DATE,
                is_trading_halt BOOLEAN,
                is_active BOOLEAN
            )
            """
        )
        con.executemany(
            "INSERT INTO universe VALUES (?, ?, 'KOSPI', 'stock', NULL, FALSE, TRUE)",
            [(symbol, symbol) for symbol in symbols],
        )
    finally:
        con.close()


def _run_strict_universe_validation(monkeypatch, snapshot, meta_db) -> None:
    monkeypatch.setattr(validate_snapshot, "META_DB", meta_db)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_snapshot",
            "--file-path",
            str(snapshot),
            "--strict",
            "--target-start",
            "2026-09-30",
            "--target-end",
            "2026-09-30",
            "--min-rows-per-year",
            "1",
            "--expected-max-date",
            "2026-09-30",
            "--completeness-start-date",
            "2026-09-30",
        ],
    )
    validate_snapshot.main()


def test_strict_validation_excludes_only_non_collectible_master_codes(tmp_path, monkeypatch) -> None:
    snapshot = tmp_path / "snapshot.parquet"
    pd.DataFrame(
        [
            {
                "symbol": "005930",
                "market": "KOSPI",
                "date": pd.Timestamp("2026-09-30"),
                "open": 100,
                "high": 110,
                "low": 90,
                "close": 105,
                "volume": 10,
                "value": 1000,
                "ingested_at": pd.Timestamp("2026-09-30T16:30:00"),
            }
        ]
    ).to_parquet(snapshot, index=False)

    supported_meta = tmp_path / "supported-meta.duckdb"
    _write_validation_meta(supported_meta, ["005930", "Q76012348"])
    _run_strict_universe_validation(monkeypatch, snapshot, supported_meta)

    missing_normal_meta = tmp_path / "missing-normal-meta.duckdb"
    _write_validation_meta(missing_normal_meta, ["005930", "000660", "Q76012348"])
    with pytest.raises(SystemExit, match="active symbols missing on max date=1"):
        _run_strict_universe_validation(monkeypatch, snapshot, missing_normal_meta)


def test_prepare_rerelease_renames_bundle_and_refreshes_companion_metadata(tmp_path) -> None:
    old_tag = "data-snapshot-20260928-1000"
    new_tag = "data-snapshot-20260928-2000"
    main = tmp_path / f"{old_tag}.parquet"
    companion = tmp_path / f"{old_tag}.ticker-state.parquet"
    sha_file = tmp_path / f"{old_tag}.sha256"
    manifest = tmp_path / f"{old_tag}.json"
    pd.DataFrame([{"symbol": "005930", "date": pd.Timestamp("2026-09-28")}]).to_parquet(main, index=False)
    pd.DataFrame([{"symbol": "005930", "snapshot_tag": old_tag}]).to_parquet(companion, index=False)
    sha_file.write_text(hashlib.sha256(main.read_bytes()).hexdigest() + "\n", encoding="utf-8")
    manifest.write_text(
        json.dumps(
            {
                "tag": old_tag,
                "file_name": main.name,
                "assets": [
                    {"name": main.name, "sha256": "old", "bytes": 0},
                    {"name": companion.name, "sha256": "old", "bytes": 0},
                ],
            }
        ),
        encoding="utf-8",
    )

    new_main = prepare_bundle(tmp_path, old_tag=old_tag, new_tag=new_tag, main_name=main.name)

    assert new_main.name == f"{new_tag}.parquet"
    assert not main.exists()
    new_companion = tmp_path / f"{new_tag}.ticker-state.parquet"
    assert pd.read_parquet(new_companion).iloc[0]["snapshot_tag"] == new_tag
    payload = json.loads((tmp_path / f"{new_tag}.json").read_text(encoding="utf-8"))
    assert payload["tag"] == new_tag
    assets = {item["name"]: item for item in payload["assets"]}
    assert assets[new_main.name]["sha256"] == hashlib.sha256(new_main.read_bytes()).hexdigest()
    assert assets[new_companion.name]["sha256"] == hashlib.sha256(new_companion.read_bytes()).hexdigest()
