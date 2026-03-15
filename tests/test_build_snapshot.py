from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.common.utils import sha256_file
from src.jobs.full_snapshot_job import build_compacted_snapshot_tag, build_full_tag
from src.transform.build_snapshot import build_snapshot, load_canonical_snapshot_df


def write_partition(base: Path, market: str, symbol: str, year: int, rows: list[dict]) -> None:
    out_dir = base / f"market={market}" / f"symbol={symbol}" / f"year={year}"
    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(out_dir / "data.parquet", index=False)


def test_load_canonical_snapshot_df_dedupes_symbol_date(tmp_path: Path) -> None:
    # 검증: 같은 symbol/date가 여러 번 있으면 최신 ingested_at 행만 snapshot에 남는다.
    silver_dir = tmp_path / "silver"
    write_partition(
        silver_dir,
        "KOSPI",
        "005930",
        2025,
        [
            {
                "symbol": "005930",
                "market": "KOSPI",
                "date": pd.Timestamp("2025-01-02"),
                "open": 100,
                "high": 110,
                "low": 90,
                "close": 101,
                "volume": 1000,
                "value": 101000,
                "ingested_at": pd.Timestamp("2026-03-14 09:00:00"),
            },
            {
                "symbol": "005930",
                "market": "KOSPI",
                "date": pd.Timestamp("2025-01-02"),
                "open": 100,
                "high": 110,
                "low": 90,
                "close": 102,
                "volume": 1200,
                "value": 122400,
                "ingested_at": pd.Timestamp("2026-03-14 10:00:00"),
            },
        ],
    )
    write_partition(
        silver_dir,
        "KOSPI",
        "000660",
        2025,
        [
            {
                "symbol": "000660",
                "market": "KOSPI",
                "date": pd.Timestamp("2025-01-03"),
                "open": 200,
                "high": 210,
                "low": 190,
                "close": 205,
                "volume": 1500,
                "value": 307500,
                "ingested_at": pd.Timestamp("2026-03-14 09:00:00"),
            }
        ],
    )

    df = load_canonical_snapshot_df(silver_dir)
    assert len(df) == 2
    assert int(df[df["symbol"] == "005930"].iloc[0]["close"]) == 102


def test_build_snapshot_writes_parquet_sha_and_manifest(tmp_path: Path) -> None:
    # 검증: build_snapshot은 parquet, sha256, manifest 파일을 모두 생성한다.
    silver_dir = tmp_path / "silver"
    output_dir = tmp_path / "snapshot"
    write_partition(
        silver_dir,
        "KOSPI",
        "005930",
        2025,
        [
            {
                "symbol": "005930",
                "market": "KOSPI",
                "date": pd.Timestamp("2025-01-02"),
                "open": 100,
                "high": 110,
                "low": 90,
                "close": 101,
                "volume": 1000,
                "value": 101000,
                "ingested_at": pd.Timestamp("2026-03-14 09:00:00"),
            }
        ],
    )

    fixed_now = datetime(2026, 3, 14, 11, 30, 0)
    result = build_snapshot(
        tag="data-snapshot-20260314-1130",
        release_type="snapshot",
        silver_dir=silver_dir,
        output_dir=output_dir,
        now_fn=lambda: fixed_now,
    )

    parquet_path = Path(result.parquet_path)
    sha_path = Path(result.sha_path)
    manifest_path = Path(result.manifest_path)
    assert parquet_path.exists()
    assert sha_path.exists()
    assert manifest_path.exists()
    assert sha_path.read_text(encoding="utf-8").strip() == sha256_file(parquet_path)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["tag"] == "data-snapshot-20260314-1130"
    assert manifest["release_type"] == "snapshot"
    assert manifest["latest_current"] is True
    assert manifest["row_count"] == 1
    assert manifest["min_date"] == "2025-01-02"
    assert manifest["max_date"] == "2025-01-02"
    assert manifest["created_at"] == "2026-03-14T11:30:00"


def test_tag_builders() -> None:
    # 검증: full/snapshot 태그 생성 규칙이 요구사항 형식과 일치한다.
    ts = datetime(2026, 3, 14, 11, 30)
    assert build_full_tag(ts) == "data-full-20260314"
    assert build_compacted_snapshot_tag(ts) == "data-snapshot-20260314-1130"
