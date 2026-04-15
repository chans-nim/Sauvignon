"""
Sauvignon silver 일봉(`data/lake/silver/ohlcv_daily`) → 백테스트용 OHLCV 로더.

프로젝트 루트는 ``backtest_mvp``의 상위 디렉터리로 가정한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd


def project_root_from_here() -> Path:
    """``backtest_mvp`` 기준 저장소 루트."""
    return Path(__file__).resolve().parent.parent


def silver_ohlcv_root(root: Path | None = None) -> Path:
    base = root or project_root_from_here()
    return base / "data" / "lake" / "silver" / "ohlcv_daily"


def load_symbol_range_silver(
    symbol: str,
    market: str,
    start_date: str,
    end_date: str,
    *,
    root: Path | None = None,
) -> pd.DataFrame:
    """
    ``src.storage.parquet_store.load_symbol_range``와 동일한 파티션 규칙.

    Returns
    -------
    원본 컬럼(symbol, market, date, open, …)을 가진 DataFrame (비어 있을 수 있음).
    """
    symbol = str(symbol).strip()
    market = str(market).strip().upper()
    start_d = pd.to_datetime(start_date).normalize()
    end_d = pd.to_datetime(end_date).normalize()
    y_lo = int(start_d.year)
    y_hi = int(end_d.year)
    base = silver_ohlcv_root(root) / f"market={market}" / f"symbol={symbol}"
    if not base.exists():
        return pd.DataFrame()

    dfs: list[pd.DataFrame] = []
    for y in range(y_lo, y_hi + 1):
        p = base / f"year={y}" / "data.parquet"
        if not p.exists():
            continue
        df = pd.read_parquet(p)
        if "date" not in df.columns:
            continue
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        dfs.append(df)

    if not dfs:
        return pd.DataFrame()

    out = pd.concat(dfs, ignore_index=True)
    out = out[(out["date"] >= start_d) & (out["date"] <= end_d)]
    return out.sort_values("date").reset_index(drop=True)


def to_backtest_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """인덱스=거래일, 열 open/high/low/close/volume."""
    if df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    need = {"open", "high", "low", "close", "volume"}
    missing = need - set(df.columns)
    if missing:
        raise ValueError(f"OHLCV columns missing: {missing}")
    x = df.copy()
    x["date"] = pd.to_datetime(x["date"]).dt.normalize()
    x = x.set_index("date")[sorted(need)]
    for c in x.columns:
        x[c] = pd.to_numeric(x[c], errors="coerce")
    x = x.dropna(how="any")
    return x.sort_index()


def load_backtest_bundle(
    specs: Sequence[tuple[str, str]] | Iterable[tuple[str, str]],
    start_date: str,
    end_date: str,
    *,
    root: Path | None = None,
    strict: bool = True,
) -> dict[str, pd.DataFrame]:
    """
    (symbol, market) 목록을 읽어 ``BacktestEngine``용 ``dict[symbol, DataFrame]`` 반환.

    Raises
    ------
    FileNotFoundError
        silver 루트가 없을 때.
    ValueError
        어떤 종목도 유효 봉이 없을 때.
    """
    root = root or project_root_from_here()
    sroot = silver_ohlcv_root(root)
    if not sroot.is_dir():
        raise FileNotFoundError(
            f"Silver OHLCV directory not found: {sroot}\n"
            "데이터 동기화: python scripts/sync_silver_from_github_release.py (또는 일봉 수집 파이프라인) 후 다시 실행하세요."
        )

    # allow iterables/generators: materialize once for error messages
    specs_list = list(specs)
    out: dict[str, pd.DataFrame] = {}
    skipped: list[tuple[str, str]] = []
    for sym, mkt in specs_list:
        raw = load_symbol_range_silver(sym, mkt, start_date, end_date, root=root)
        ohlc = to_backtest_ohlcv(raw)
        if ohlc.empty:
            if strict:
                raise ValueError(
                    f"No silver bars for {sym} ({mkt}) in [{start_date}, {end_date}]. "
                    f"Expected under {sroot / f'market={mkt.upper()}' / f'symbol={sym}'}"
                )
            skipped.append((sym, mkt))
            continue
        out[sym] = ohlc

    if not out:
        raise ValueError(
            f"No usable symbols loaded for [{start_date}, {end_date}] strict={strict}. "
            f"Checked {len(specs_list)} specs."
        )

    return out


def list_available_symbols(market: str, *, root: Path | None = None) -> list[str]:
    """silver 파티션에서 사용 가능한 심볼(종목코드) 목록을 반환한다."""
    root = root or project_root_from_here()
    mkt = str(market).strip().upper()
    base = silver_ohlcv_root(root) / f"market={mkt}"
    if not base.is_dir():
        return []
    out: list[str] = []
    for p in base.iterdir():
        if not p.is_dir():
            continue
        name = p.name
        if name.startswith("symbol="):
            out.append(name.split("=", 1)[1])
    return sorted(out)
