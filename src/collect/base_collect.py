from __future__ import annotations
from typing import TYPE_CHECKING, List, Tuple
import pandas as pd

from src.common.utils import chunk_date_ranges

if TYPE_CHECKING:
    from src.clients.kis_client import KISClient


def _price_field(row: dict, primary: str, fallback: str):
    value = row.get(primary)
    return row.get(fallback) if value is None or value == "" else value


def normalize_ohlcv(symbol: str, market: str, payload: dict) -> pd.DataFrame:
    rows = payload.get("output2") or payload.get("output") or []
    if not rows:
        return pd.DataFrame(columns=["symbol","market","date","open","high","low","close","volume","value","ingested_at"])
    norm = []
    now = pd.Timestamp.now()
    for r in rows:
        date_str = r.get("stck_bsop_date") or r.get("xymd") or r.get("date")
        if not date_str:
            continue
        norm.append({
            "symbol": symbol,
            "market": market,
            "date": pd.to_datetime(date_str, format="%Y%m%d", errors="coerce"),
            "open": pd.to_numeric(_price_field(r, "stck_oprc", "open"), errors="coerce"),
            "high": pd.to_numeric(_price_field(r, "stck_hgpr", "high"), errors="coerce"),
            "low": pd.to_numeric(_price_field(r, "stck_lwpr", "low"), errors="coerce"),
            "close": pd.to_numeric(_price_field(r, "stck_clpr", "close"), errors="coerce"),
            "volume": pd.to_numeric(_price_field(r, "acml_vol", "volume"), errors="coerce"),
            "value": pd.to_numeric(_price_field(r, "acml_tr_pbmn", "value"), errors="coerce"),
            "ingested_at": now,
        })
    columns = ["symbol", "market", "date", "open", "high", "low", "close", "volume", "value", "ingested_at"]
    df = pd.DataFrame(norm, columns=columns).dropna(subset=["date"])
    # An explicit dated no-trade row can carry a close but zero OHLC.
    # Use that same row's close only when all three prices and activity are zero;
    # missing fields and inconsistent traded rows must still fail validation.
    no_trade = (df["close"] > 0) & (df["volume"] == 0) & (df["value"] == 0)
    no_trade &= (df[["open", "high", "low"]] == 0).all(axis=1)
    for column in ("open", "high", "low"):
        df.loc[no_trade, column] = df.loc[no_trade, "close"]
    return df.sort_values("date").reset_index(drop=True)

def validate_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.drop_duplicates(subset=["symbol", "date"]).copy()
    df = df[(df["close"] > 0) & (df["volume"] >= 0)].copy()
    df = df[(df["low"] <= df["open"]) & (df["low"] <= df["close"]) & (df["high"] >= df["open"]) & (df["high"] >= df["close"])].copy()
    return df.sort_values("date").reset_index(drop=True)


def _chunk_coverage_ratio(existing_df: pd.DataFrame, c_start: str, c_end: str) -> float:
    """청크 구간 [c_start, c_end]에 대해 기존 데이터가 얼마나 채워져 있는지 비율(0~1) 반환."""
    if existing_df is None or existing_df.empty:
        return 0.0
    start_d = pd.to_datetime(c_start).normalize()
    end_d = pd.to_datetime(c_end).normalize()
    if "date" not in existing_df.columns:
        return 0.0
    dates = pd.to_datetime(existing_df["date"]).dt.normalize()
    mask = (dates >= start_d) & (dates <= end_d)
    n_have = mask.sum()
    if n_have == 0:
        return 0.0
    cal_days = (end_d - start_d).days + 1
    # 연간 거래일 비율로 기대 거래일 수 추정
    expected = max(1, int(cal_days * 252 / 365))
    return min(1.0, n_have / expected)


def fetch_ohlcv_chunked(
    client: "KISClient",
    symbol: str,
    market: str,
    start_date: str,
    end_date: str,
    chunk_days: int = 365,
    existing_df: pd.DataFrame | None = None,
    coverage_threshold: float = 0.7,
    force_fetch_end_date: bool = False,
) -> Tuple[pd.DataFrame, List[Tuple[str, str, dict]], List[Tuple[str, str, float]]]:
    """
    chunk_days 단위로 나눠, 이미 기존 데이터(existing_df)로 충분히 채워진 청크는 API 호출 없이
    재사용하고, 부족한 청크만 API 호출 후 결과를 합쳐 반환한다.
    - existing_df: 기존 silver에서 읽은 구간 데이터 (없으면 None).
    - coverage_threshold: 청크 내 기대 거래일 대비 기존 데이터 비율이 이 값 이상이면 API 스킵 (0.7 = 70%).
    - force_fetch_end_date: end_date가 포함된 마지막 청크는 기존 데이터가 있어도 반드시 다시 조회한다.
      장중 데이터가 이미 존재한다는 이유로 마감 시세 갱신을 건너뛰지 않기 위한 옵션이다.
    반환: (combined_df, fetched_payloads, skipped_with_ratio)
          skipped_with_ratio = [(c_start, c_end, ratio), ...]  # 스킵된 청크와 그 커버리지 비율.
    """
    dfs: List[pd.DataFrame] = []
    raw_payloads: List[Tuple[str, str, dict]] = []
    skipped_with_ratio: List[Tuple[str, str, float]] = []
    for c_start, c_end in chunk_date_ranges(start_date, end_date, chunk_days):
        must_fetch = force_fetch_end_date and c_start <= end_date <= c_end
        if not must_fetch and existing_df is not None and not existing_df.empty:
            ratio = _chunk_coverage_ratio(existing_df, c_start, c_end)
            if ratio >= coverage_threshold:
                start_d = pd.to_datetime(c_start).normalize()
                end_d = pd.to_datetime(c_end).normalize()
                dates = pd.to_datetime(existing_df["date"]).dt.normalize()
                mask = (dates >= start_d) & (dates <= end_d)
                part = existing_df.loc[mask].copy()
                if not part.empty:
                    dfs.append(part)
                skipped_with_ratio.append((c_start, c_end, ratio))
                continue
        payload = client.get_daily_ohlcv(symbol=symbol, start_date=c_start, end_date=c_end)
        raw_payloads.append((c_start, c_end, payload))
        part = normalize_ohlcv(symbol, market, payload)
        if not part.empty:
            dfs.append(part)
    if not dfs:
        empty = pd.DataFrame(
            columns=["symbol", "market", "date", "open", "high", "low", "close", "volume", "value", "ingested_at"]
        )
        return empty, raw_payloads, skipped_with_ratio
    combined = pd.concat(dfs, ignore_index=True)
    combined = combined.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
    return combined, raw_payloads, skipped_with_ratio
