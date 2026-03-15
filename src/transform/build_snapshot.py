from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Callable

import duckdb
import pandas as pd

from src.common.settings import settings
from src.common.utils import sha256_file

SILVER_DIR = settings.project_root / "data" / "lake" / "silver" / "ohlcv_daily"
SNAPSHOT_DIR = settings.project_root / "data" / "snapshot"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

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
    }


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

    df.to_parquet(parquet_path, index=False)
    sha = hash_fn(parquet_path)
    sha_path.write_text(sha + "\n", encoding="utf-8")

    created_at = now_fn().isoformat(timespec="seconds")
    min_date = None if df.empty else str(pd.to_datetime(df["date"]).min().date())
    max_date = None if df.empty else str(pd.to_datetime(df["date"]).max().date())
    manifest = build_snapshot_manifest(
        tag=tag,
        release_type=release_type,
        parquet_path=parquet_path,
        sha256=sha,
        row_count=int(len(df)),
        min_date=min_date,
        max_date=max_date,
        created_at=created_at,
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

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
