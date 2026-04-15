"""백테스트 실행: 합성 샘플 또는 Sauvignon silver 일봉 실데이터."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from backtest_engine import BacktestEngine
from config import BacktestConfig
from interpretation import RunContext, build_interpretation_ko
from report import write_all_reports
from silver_data import load_backtest_bundle, project_root_from_here


def make_sample_ohlcv(
    symbols: list[str],
    n_days: int = 220,
    seed: int = 42,
) -> dict[str, pd.DataFrame]:
    """최소 3종목, 200일 이상 동일 캘린더 OHLCV."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=pd.Timestamp("2026-03-31"), periods=n_days)
    out: dict[str, pd.DataFrame] = {}
    for i, sym in enumerate(symbols):
        drift = 0.00015 + 0.00005 * i
        vol = 0.012 + 0.002 * i
        r = rng.normal(drift, vol, size=len(dates))
        close = 50_000.0 * np.exp(np.cumsum(r))
        noise = rng.uniform(0.995, 1.005, size=len(dates))
        open_ = np.r_[close[0], close[:-1]] * noise
        high = np.maximum(open_, close) * rng.uniform(1.0, 1.02, size=len(dates))
        low = np.minimum(open_, close) * rng.uniform(0.98, 1.0, size=len(dates))
        volume = rng.integers(80_000, 500_000, size=len(dates)).astype(float)
        df = pd.DataFrame(
            {
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            },
            index=dates,
        )
        out[sym] = df
    return out


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="포트폴리오 백테스트 (샘플 또는 silver 실데이터)",
    )
    p.add_argument(
        "--data",
        choices=["sample", "silver"],
        default="sample",
        help="sample=합성 OHLCV, silver=data/lake/silver/ohlcv_daily 일봉",
    )
    p.add_argument("--start", default="2019-01-02", help="silver 시작일 (YYYY-MM-DD)")
    p.add_argument("--end", default="2025-12-31", help="silver 종료일 (YYYY-MM-DD)")
    p.add_argument(
        "--symbols",
        nargs="+",
        default=["005930", "000660", "035420"],
        help="종목 코드 (6자리)",
    )
    p.add_argument(
        "--market",
        default="KOSPI",
        help="silver 로드 시 시장 코드 (종목 공통, 예: KOSPI / KOSDAQ)",
    )
    p.add_argument(
        "--root",
        type=Path,
        default=None,
        help="저장소 루트 (기본: backtest_mvp 상위 폴더)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

    args = _parse_args(argv)
    root = args.root.resolve() if args.root else project_root_from_here()

    cfg = BacktestConfig(
        initial_capital=50_000_000.0,
        max_positions=3,
        allocation_per_position=12_000_000.0,
    )

    if args.data == "sample":
        data = make_sample_ohlcv(list(args.symbols), n_days=220, seed=7)
        start_s = str(data[args.symbols[0]].index.min().date())
        end_s = str(data[args.symbols[0]].index.max().date())
        src = "합성 샘플 OHLCV (랜덤 워크)"
    else:
        try:
            specs = [(s.strip(), str(args.market).strip().upper()) for s in args.symbols]
            data = load_backtest_bundle(specs, args.start, args.end, root=root)
        except (FileNotFoundError, ValueError) as e:
            print(str(e), file=sys.stderr)
            return 1
        start_s, end_s = args.start, args.end
        src = f"Silver 일봉 ({root / 'data' / 'lake' / 'silver' / 'ohlcv_daily'})"

    engine = BacktestEngine(cfg, data)
    result = engine.run()

    trade_log_df = result.trade_log_df
    equity_curve_df = result.equity_curve_df
    summary = result.summary

    ctx = RunContext(
        data_source=src,
        start_date=start_s,
        end_date=end_s,
        symbols=tuple(str(s) for s in args.symbols),
        n_trades=int(len(trade_log_df)),
        n_equity_days=int(len(equity_curve_df)),
    )
    interpretation = build_interpretation_ko(summary, ctx)

    out_dir = Path(__file__).resolve().parent / "output"
    write_all_reports(
        trade_log_df,
        equity_curve_df,
        summary,
        out_dir,
        interpretation_md=interpretation,
    )

    print(f"\nRows trade_log: {len(trade_log_df)}, equity: {len(equity_curve_df)}")
    print("Performance dict:", summary.to_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
