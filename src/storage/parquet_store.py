from __future__ import annotations
from pathlib import Path
from typing import List
import pandas as pd
from src.common.settings import settings

RAW_OHLCV_DIR = settings.project_root / "data" / "raw" / "ohlcv"
SILVER_DIR = settings.project_root / "data" / "lake" / "silver" / "ohlcv_daily"
RAW_OHLCV_DIR.mkdir(parents=True, exist_ok=True)
SILVER_DIR.mkdir(parents=True, exist_ok=True)


def load_symbol_range(symbol: str, market: str, start_date: str, end_date: str) -> pd.DataFrame:
    """
    기존 silver 파티션에서 해당 종목의 [start_date, end_date] 구간 데이터를 읽어 반환한다.
    없으면 빈 DataFrame 반환.
    """
    symbol = str(symbol).strip()
    market = str(market).strip()
    start_d = pd.to_datetime(start_date).normalize()
    end_d = pd.to_datetime(end_date).normalize()
    y_lo = start_d.year
    y_hi = end_d.year
    base = SILVER_DIR / f"market={market}" / f"symbol={symbol}"
    if not base.exists():
        return pd.DataFrame(
            columns=["symbol", "market", "date", "open", "high", "low", "close", "volume", "value", "ingested_at"]
        )
    dfs: List[pd.DataFrame] = []
    for y in range(y_lo, y_hi + 1):
        p = base / f"year={y}" / "data.parquet"
        if not p.exists():
            continue
        df = pd.read_parquet(p)
        if "date" not in df.columns:
            continue
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        dfs.append(df)
    if not dfs:
        return pd.DataFrame(
            columns=["symbol", "market", "date", "open", "high", "low", "close", "volume", "value", "ingested_at"]
        )
    out = pd.concat(dfs, ignore_index=True)
    out = out[(out["date"] >= start_d) & (out["date"] <= end_d)]
    return out.sort_values("date").reset_index(drop=True)

def save_raw_json(symbol: str, payload: dict, suffix: str | None = None) -> Path:
    import json
    from datetime import datetime
    run_date = datetime.now().strftime("%Y-%m-%d")
    out_dir = RAW_OHLCV_DIR / run_date
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{symbol}_{suffix}.json" if suffix else f"{symbol}.json"
    out_path = out_dir / fname
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def _empty_ohlcv_df() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["symbol", "market", "date", "open", "high", "low", "close", "volume", "value", "ingested_at"]
    )


def _normalize_ohlcv_df(df: pd.DataFrame) -> pd.DataFrame:
    required = {"symbol", "market", "date"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"missing required columns for upsert: {missing}")
    work = df.copy()
    work["symbol"] = work["symbol"].astype(str).str.strip()
    work["market"] = work["market"].astype(str).str.strip()
    work["date"] = pd.to_datetime(work["date"]).dt.normalize()
    return work


def upsert_ohlcv_from_df(df: pd.DataFrame) -> List[Path]:
    """
    일봉 DataFrame 을 silver 파티션에 upsert 한다.
    - 파티션 키: market / symbol / year
    - 중복 키: symbol / date (최신 값 유지)
    """
    saved: List[Path] = []
    if df.empty:
        return saved
    work = _normalize_ohlcv_df(df)
    work["_upsert_rank"] = 1
    for (market, symbol, year), year_df in work.groupby(
        [work["market"], work["symbol"], work["date"].dt.year], sort=True
    ):
        out_dir = SILVER_DIR / f"market={market}" / f"symbol={symbol}" / f"year={year}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "data.parquet"
        if out_path.exists():
            old = _normalize_ohlcv_df(pd.read_parquet(out_path))
            old["_upsert_rank"] = 0
            merged = pd.concat([old, year_df], ignore_index=True)
            merged = merged.sort_values(["date", "_upsert_rank"], kind="mergesort")
            merged = merged.drop_duplicates(subset=["symbol", "date"], keep="last")
        else:
            merged = year_df.sort_values(["date", "_upsert_rank"], kind="mergesort")
            merged = merged.drop_duplicates(subset=["symbol", "date"], keep="last")
        merged = merged.drop(columns=["_upsert_rank"], errors="ignore")
        merged.to_parquet(out_path, index=False)
        saved.append(out_path)
    return saved


def write_symbol_parquet(df: pd.DataFrame) -> List[Path]:
    return upsert_ohlcv_from_df(df)
