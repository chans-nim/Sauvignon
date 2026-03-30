from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Callable

import duckdb
import pandas as pd

from src.common.settings import settings
from src.common.utils import sha256_file
from src.storage import meta_store

SILVER_DIR = settings.project_root / "data" / "lake" / "silver" / "ohlcv_daily"
SNAPSHOT_DIR = settings.project_root / "data" / "snapshot"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
RAW_OHLCV_DIR = settings.project_root / "data" / "raw" / "ohlcv"

OHLCV_COLUMNS = [
    "symbol",
    "market",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "value",
    "ingested_at",
]


@dataclass(frozen=True)
class SnapshotBuildResult:
    tag: str
    release_type: str
    file_name: str
    file_path: str
    sha256: str
    row_count: int
    min_date: str | None
    max_date: str | None
    created_at: str
    latest_current: bool
    parquet_path: str
    sha_path: str
    manifest_path: str
    companion_assets: list[dict]


def empty_snapshot_df() -> pd.DataFrame:
    return pd.DataFrame(columns=OHLCV_COLUMNS)


def silver_parquet_paths(silver_dir: Path = SILVER_DIR) -> list[str]:
    if not silver_dir.exists():
        return []
    return [p.as_posix() for p in silver_dir.rglob("data.parquet")]


def load_canonical_snapshot_df(silver_dir: Path = SILVER_DIR) -> pd.DataFrame:
    paths = silver_parquet_paths(silver_dir)
    if not paths:
        return empty_snapshot_df()

    con = duckdb.connect()
    try:
        df = con.execute("SELECT * FROM read_parquet(?)", [paths]).fetchdf()
    finally:
        con.close()

    if df.empty:
        return empty_snapshot_df()

    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    if "ingested_at" in df.columns:
        df["ingested_at"] = pd.to_datetime(df["ingested_at"], errors="coerce")
    else:
        df["ingested_at"] = pd.NaT

    for col in OHLCV_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA

    work = df[OHLCV_COLUMNS].copy()
    work = work.sort_values(["market", "symbol", "date", "ingested_at"], kind="mergesort")
    work = work.drop_duplicates(subset=["symbol", "date"], keep="last")
    return work.sort_values(["market", "symbol", "date"], kind="mergesort").reset_index(drop=True)


def build_snapshot_manifest(
    *,
    tag: str,
    release_type: str,
    parquet_path: Path,
    sha256: str,
    row_count: int,
    min_date: str | None,
    max_date: str | None,
    created_at: str,
    assets: list[dict],
) -> dict:
    return {
        "tag": tag,
        "release_type": release_type,
        "latest_current": True,
        "file_name": parquet_path.name,
        "file_path": parquet_path.as_posix(),
        "sha256": sha256,
        "row_count": row_count,
        "min_date": min_date,
        "max_date": max_date,
        "created_at": created_at,
        "assets": assets,
    }


def _asset_entry(path: Path, *, release_type: str, created_at: str, sha: str | None = None) -> dict:
    asset_sha = sha or sha256_file(path)
    return {
        "name": path.name,
        "sha256": asset_sha,
        "bytes": int(path.stat().st_size),
        "updated_at": created_at,
        "release_type": release_type,
    }


def build_ticker_state_df() -> pd.DataFrame:
    meta_store.ensure_tables()
    con = meta_store.connect()
    try:
        return con.execute(
            """
            SELECT
              u.symbol,
              u.std_code,
              u.name,
              u.market,
              u.asset_type,
              u.listing_date,
              u.is_etf,
              u.is_spac,
              u.is_trading_halt,
              u.is_admin_issue,
              u.is_warning,
              u.is_active,
              u.updated_at AS universe_updated_at,
              c.last_success_date,
              c.last_attempt_at,
              c.retry_count,
              c.last_error,
              c.updated_at AS collect_state_updated_at
            FROM universe u
            LEFT JOIN collect_state c
              ON u.symbol = c.symbol
             AND c.timeframe = '1d'
            ORDER BY u.market, u.symbol
            """
        ).fetchdf()
    finally:
        con.close()


def build_output1_latest_df() -> pd.DataFrame:
    rows: list[dict] = []
    if not RAW_OHLCV_DIR.exists():
        return pd.DataFrame()

    latest_by_symbol: dict[str, Path] = {}
    for day_dir in RAW_OHLCV_DIR.iterdir():
        if not day_dir.is_dir():
            continue
        for f in day_dir.glob("*.json"):
            symbol = f.stem.split("_", 1)[0].strip()
            if not symbol:
                continue
            prev = latest_by_symbol.get(symbol)
            if prev is None or (prev.parent.name, prev.name) < (f.parent.name, f.name):
                latest_by_symbol[symbol] = f

    if not latest_by_symbol:
        return pd.DataFrame()

    normalized_keys: set[str] = set()
    for symbol, path in sorted(latest_by_symbol.items()):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        output1 = payload.get("output1") or {}
        if not isinstance(output1, dict):
            output1 = {}
        normalized: dict[str, object] = {}
        for k, v in output1.items():
            nk = f"output1__{str(k).strip()}"
            normalized[nk] = v
            normalized_keys.add(nk)
        rows.append(
            {
                "symbol": symbol,
                "raw_file_path": path.as_posix(),
                "raw_file_date": path.parent.name,
                "rt_cd": payload.get("rt_cd"),
                "msg_cd": payload.get("msg_cd"),
                "msg1": payload.get("msg1"),
                "output1_json": json.dumps(output1, ensure_ascii=False, sort_keys=True),
                **normalized,
            }
        )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    for key in sorted(normalized_keys):
        if key not in df.columns:
            df[key] = pd.NA
    return df.sort_values("symbol").reset_index(drop=True)


def write_snapshot_bundle(
    df: pd.DataFrame,
    *,
    tag: str,
    release_type: str,
    output_dir: Path = SNAPSHOT_DIR,
    hash_fn: Callable[[Path], str] = sha256_file,
    now_fn: Callable[[], datetime] = datetime.now,
) -> SnapshotBuildResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = output_dir / f"{tag}.parquet"
    sha_path = output_dir / f"{tag}.sha256"
    manifest_path = output_dir / f"{tag}.json"
    ticker_state_path = output_dir / f"{tag}.ticker-state.parquet"
    output1_latest_path = output_dir / f"{tag}.output1-latest.parquet"

    df.to_parquet(parquet_path, index=False)
    sha = hash_fn(parquet_path)
    sha_path.write_text(sha + "\n", encoding="utf-8")

    created_at = now_fn().isoformat(timespec="seconds")
    min_date = None if df.empty else str(pd.to_datetime(df["date"]).min().date())
    max_date = None if df.empty else str(pd.to_datetime(df["date"]).max().date())
    companion_assets: list[dict] = []
    main_asset = _asset_entry(parquet_path, release_type=release_type, created_at=created_at, sha=sha)
    companion_assets.append(main_asset)
    companion_assets.append(_asset_entry(sha_path, release_type=release_type, created_at=created_at))

    ticker_state_df = build_ticker_state_df()
    if not ticker_state_df.empty:
        ticker_state_df["snapshot_tag"] = tag
        ticker_state_df["snapshot_created_at"] = created_at
        ticker_state_df.to_parquet(ticker_state_path, index=False)
        companion_assets.append(_asset_entry(ticker_state_path, release_type=release_type, created_at=created_at))

    output1_latest_df = build_output1_latest_df()
    if not output1_latest_df.empty:
        output1_latest_df["snapshot_tag"] = tag
        output1_latest_df["snapshot_created_at"] = created_at
        output1_latest_df.to_parquet(output1_latest_path, index=False)
        companion_assets.append(_asset_entry(output1_latest_path, release_type=release_type, created_at=created_at))

    manifest = build_snapshot_manifest(
        tag=tag,
        release_type=release_type,
        parquet_path=parquet_path,
        sha256=sha,
        row_count=int(len(df)),
        min_date=min_date,
        max_date=max_date,
        created_at=created_at,
        assets=companion_assets.copy(),
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    companion_assets.append(_asset_entry(manifest_path, release_type=release_type, created_at=created_at))

    return SnapshotBuildResult(
        tag=tag,
        release_type=release_type,
        file_name=parquet_path.name,
        file_path=parquet_path.as_posix(),
        sha256=sha,
        row_count=int(len(df)),
        min_date=min_date,
        max_date=max_date,
        created_at=created_at,
        latest_current=True,
        parquet_path=parquet_path.as_posix(),
        sha_path=sha_path.as_posix(),
        manifest_path=manifest_path.as_posix(),
        companion_assets=companion_assets,
    )


def build_snapshot(
    *,
    tag: str,
    release_type: str,
    silver_dir: Path = SILVER_DIR,
    output_dir: Path = SNAPSHOT_DIR,
    hash_fn: Callable[[Path], str] = sha256_file,
    now_fn: Callable[[], datetime] = datetime.now,
) -> SnapshotBuildResult:
    df = load_canonical_snapshot_df(silver_dir=silver_dir)
    return write_snapshot_bundle(
        df,
        tag=tag,
        release_type=release_type,
        output_dir=output_dir,
        hash_fn=hash_fn,
        now_fn=now_fn,
    )
