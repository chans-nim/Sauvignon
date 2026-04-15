"""
Strategy Builder 수준의 '여러 전략 시험'을 위한 배치 러너.

- 10개 프리셋 전략 레지스트리 기반으로 백테스트를 반복 실행
- 결과를 CSV/MD로 저장
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pandas as pd

from backtest_engine import BacktestEngine
from config import BacktestConfig
from interpretation import RunContext, build_interpretation_ko
from report import write_all_reports
from silver_data import load_backtest_bundle, project_root_from_here, list_available_symbols

from strategy_core.registry import StrategyRegistry
from strategy_core.preset import presets  # noqa: F401  (register side-effects)
from strategy_lab_html import final_ranking_from_top_pool, write_strategy_lab_html


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run preset strategy lab (grid-ready skeleton).")
    p.add_argument("--start", default="2020-01-01")
    p.add_argument("--end", default="2024-12-31")
    p.add_argument("--symbols", nargs="+", default=["005930", "000660", "035420"])
    p.add_argument("--all-symbols", action="store_true", help="Use all available symbols in the given market.")
    p.add_argument("--max-symbols", type=int, default=0, help="Cap number of symbols (0 = no cap).")
    p.add_argument("--market", default="KOSPI")
    p.add_argument("--root", type=Path, default=None)
    p.add_argument("--out", type=Path, default=None, help="output dir (default backtest_mvp/output/strategy_lab)")
    p.add_argument(
        "--optimize",
        default=None,
        help="Grid search for a single strategy_id (e.g. golden_cross) or 'all' for all default grids. If omitted, run all presets once.",
    )
    p.add_argument(
        "--grid",
        action="append",
        default=[],
        help="Grid param spec. Repeatable. Example: --grid fast=3,5,10 --grid slow=20,60",
    )
    p.add_argument(
        "--list-grids",
        action="store_true",
        help="Print built-in default grids and exit.",
    )
    p.add_argument("--max-combos", type=int, default=0, help="Cap grid combinations per strategy (0 = no cap).")
    p.add_argument("--max-seconds", type=float, default=0.0, help="Wall-clock time budget (0 = no limit).")
    p.add_argument(
        "--jobs",
        type=int,
        default=0,
        help="Parallel workers for (strategy,param) evaluation. 0 = auto, 1 = sequential.",
    )
    p.add_argument(
        "--top-k-per-strategy",
        type=int,
        default=3,
        help="Per-strategy top-K by CAGR used for the 'final ranking' pool (option B).",
    )
    p.add_argument(
        "--max-symbol-charts",
        type=int,
        default=100,
        help="Max number of per-symbol OHLC charts in the HTML report (traded symbols first).",
    )
    p.add_argument(
        "--no-html",
        action="store_true",
        help="Skip writing strategy_lab_report.html.",
    )
    return p.parse_args(argv)


def _cast_scalar(x: str) -> int | float | str:
    s = str(x).strip()
    try:
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        return s


def _parse_grid(items: list[str]) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {}
    for it in items:
        if "=" not in it:
            raise ValueError(f"Invalid --grid spec (expected k=v1,v2,..): {it!r}")
        k, v = it.split("=", 1)
        key = k.strip()
        vals = [x.strip() for x in v.split(",") if x.strip() != ""]
        if not key or not vals:
            raise ValueError(f"Invalid --grid spec: {it!r}")
        out[key] = [_cast_scalar(x) for x in vals]
    return out


def _grid_product(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    if not grid:
        return [dict()]
    keys = list(grid.keys())
    combos: list[dict[str, Any]] = []
    def rec(i: int, cur: dict[str, Any]) -> None:
        if i >= len(keys):
            combos.append(dict(cur))
            return
        k = keys[i]
        for v in grid[k]:
            cur[k] = v
            rec(i + 1, cur)
        cur.pop(k, None)
    rec(0, {})
    return combos


DEFAULT_GRIDS: dict[str, dict[str, list[Any]]] = {
    # trend
    "golden_cross": {"fast": [3, 5, 10], "slow": [20, 60, 120]},
    "trend_filter": {"ma": [20, 60, 120]},
    "momentum": {"lookback": [5, 10, 20, 60, 120]},
    "consecutive": {"buy_days": [2, 3, 5, 7]},
    # breakout
    "week52_high": {"window": [126, 252], "exit_ma": [10, 20]},
    "volatility": {"vol_window": [10, 20], "low_window": [40, 60, 120], "exit_ma": [10, 20]},
    # mean reversion
    "disparity": {"ma": [10, 20, 60], "buy_below": [-0.04, -0.06, -0.08], "sell_above": [-0.02, -0.01, 0.0]},
    "mean_reversion": {"window": [10, 20, 60], "z_buy": [-1.0, -1.5, -2.0], "z_sell": [-0.5, -0.2, 0.0]},
    # risk-ish
    "breakout_fail": {"lookback": [20, 60, 120], "entry_ma": [10, 20]},
    "strong_close": {"threshold": [0.7, 0.8, 0.9], "exit_ma": [10, 20]},
    # baseline wrapper (no params)
    "ma_volume": {},
}


def _default_grid_for(strategy_id: str) -> dict[str, list[Any]]:
    return DEFAULT_GRIDS.get(strategy_id, {})


def run_once(
    strategy_id: str,
    data: dict[str, pd.DataFrame],
    base_cfg: BacktestConfig,
    *,
    params: dict[str, Any] | None = None,
) -> dict[str, object]:
    strat = StrategyRegistry.create(strategy_id, **(params or {}))

    def provider(ohlcv: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
        return strat.build(ohlcv, cfg)

    engine = BacktestEngine(base_cfg, data, signal_provider=provider)
    res = engine.run()
    summ = res.summary.to_dict()
    row: dict[str, object] = {"strategy_id": strategy_id, **summ}
    if params:
        row["params_json"] = json.dumps(params, ensure_ascii=False, sort_keys=True)
    return row


_WORKER_DATA_CACHE: dict[str, dict[str, pd.DataFrame]] = {}


def _cache_key(root: str, market: str, start: str, end: str, symbols: list[str]) -> str:
    import hashlib

    h = hashlib.sha256()
    h.update(root.encode("utf-8"))
    h.update(b"|")
    h.update(market.encode("utf-8"))
    h.update(b"|")
    h.update(start.encode("utf-8"))
    h.update(b"|")
    h.update(end.encode("utf-8"))
    h.update(b"|")
    h.update(",".join(symbols).encode("utf-8"))
    return h.hexdigest()[:16]


def _worker_eval(payload: dict[str, Any]) -> dict[str, object]:
    """
    Worker process: loads silver data once per-process (cache) and evaluates one (strategy_id, params) combo.
    Windows spawn 환경에서도 동작하도록, 필요한 정보를 payload로 전달한다.
    """
    root = str(payload["root"])
    market = str(payload["market"])
    start = str(payload["start"])
    end = str(payload["end"])
    symbols = list(payload["symbols"])
    strict = bool(payload["strict"])
    strategy_id = str(payload["strategy_id"])
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        params = {}

    key = _cache_key(root, market, start, end, symbols)
    data = _WORKER_DATA_CACHE.get(key)
    if data is None:
        specs = [(s, market) for s in symbols]
        data = load_backtest_bundle(specs, start, end, root=Path(root), strict=strict)
        _WORKER_DATA_CACHE[key] = data

    cfg = BacktestConfig(
        initial_capital=float(payload["initial_capital"]),
        max_positions=int(payload["max_positions"]),
        allocation_per_position=float(payload["allocation_per_position"]),
    )

    try:
        return run_once(strategy_id, data, cfg, params=params)
    except Exception as e:
        return {
            "strategy_id": strategy_id,
            "params_json": json.dumps(params, ensure_ascii=False, sort_keys=True),
            "error": str(e),
        }


def _top_k_per_strategy(df: pd.DataFrame, k: int = 3) -> pd.DataFrame:
    """error 제외 후, strategy_id별 CAGR 상위 k개."""
    if df.empty:
        return df
    ok = df[df.get("error").isna()] if "error" in df.columns else df
    if ok.empty or "cagr" not in ok.columns:
        return ok
    return (
        ok.sort_values(["strategy_id", "cagr"], ascending=[True, False])
        .groupby("strategy_id", as_index=False, sort=True)
        .head(k)
        .reset_index(drop=True)
    )


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

    args = _parse_args(argv)

    if args.list_grids:
        for sid in StrategyRegistry.ids():
            g = _default_grid_for(sid)
            print(f"{sid}: {g}")
        return 0

    root = args.root.resolve() if args.root else project_root_from_here()
    out_dir = args.out or (Path(__file__).resolve().parent / "output" / "strategy_lab")
    out_dir.mkdir(parents=True, exist_ok=True)

    market = str(args.market).strip().upper()
    if args.all_symbols:
        symbols = list_available_symbols(market, root=root)
        if not symbols:
            raise SystemExit(f"No symbols found for market={market} under silver lake.")
        if args.max_symbols and args.max_symbols > 0:
            symbols = symbols[: int(args.max_symbols)]
    else:
        symbols = [str(s).strip() for s in args.symbols]

    specs = [(s, market) for s in symbols]
    strict = not args.all_symbols
    data = load_backtest_bundle(specs, args.start, args.end, root=root, strict=strict)

    cfg = BacktestConfig(initial_capital=50_000_000.0, max_positions=3, allocation_per_position=12_000_000.0)

    # budgets / jobs
    t_start = time.time()
    budget_s = float(args.max_seconds or 0.0)

    def budget_ok() -> bool:
        return budget_s <= 0 or (time.time() - t_start) <= budget_s

    jobs = int(args.jobs or 0)
    if jobs <= 0:
        jobs = max(1, (os.cpu_count() or 4) - 1)

    rows: list[dict[str, object]] = []
    if args.optimize:
        target = str(args.optimize).strip()
        strategy_ids = StrategyRegistry.ids() if target.lower() == "all" else [target]

        tasks: list[dict[str, Any]] = []
        for sid in strategy_ids:
            if not budget_ok():
                break
            grid = _parse_grid(list(args.grid)) if args.grid else _default_grid_for(sid)
            combos = _grid_product(grid)
            if args.max_combos and args.max_combos > 0:
                combos = combos[: int(args.max_combos)]
            for pset in combos:
                if not budget_ok():
                    break
                tasks.append(
                    {
                        "root": str(root),
                        "market": market,
                        "start": str(args.start),
                        "end": str(args.end),
                        "symbols": symbols,
                        "strict": strict,
                        "strategy_id": sid,
                        "params": pset,
                        "initial_capital": cfg.initial_capital,
                        "max_positions": cfg.max_positions,
                        "allocation_per_position": cfg.allocation_per_position,
                    }
                )

        print(f"[plan] strategies={len(strategy_ids)} tasks={len(tasks)} jobs={jobs} symbols={len(symbols)}", flush=True)

        if jobs == 1:
            for t in tasks:
                if not budget_ok():
                    break
                try:
                    rows.append(run_once(t["strategy_id"], data, cfg, params=t.get("params")))
                except Exception as e:
                    rows.append(
                        {
                            "strategy_id": str(t["strategy_id"]),
                            "params_json": json.dumps(t.get("params") or {}, ensure_ascii=False, sort_keys=True),
                            "error": str(e),
                        }
                    )
        else:
            # Important: on Windows, processes use spawn; worker loads data and caches per-process.
            with ProcessPoolExecutor(max_workers=jobs) as ex:
                futures = []
                for t in tasks:
                    if not budget_ok():
                        break
                    futures.append(ex.submit(_worker_eval, t))
                for fut in as_completed(futures):
                    try:
                        rows.append(fut.result())
                    except Exception as e:
                        rows.append({"strategy_id": "UNKNOWN", "error": str(e)})
    else:
        for sid in StrategyRegistry.ids():
            try:
                rows.append(run_once(sid, data, cfg))
            except Exception as e:
                rows.append({"strategy_id": sid, "error": str(e)})

    df = pd.DataFrame(rows)
    df.to_csv(out_dir / "strategy_lab_summary.csv", index=False)

    # best by CAGR (ignore errors)
    ok = df[df.get("error").isna()] if "error" in df.columns else df
    if not ok.empty and "cagr" in ok.columns:
        best = ok.sort_values(["cagr"], ascending=False).iloc[0].to_dict()
        best_id = str(best["strategy_id"])
        best_params_json = str(best.get("params_json", "")) if "params_json" in ok.columns else ""
    else:
        best_id = "ma_volume"
        best_params_json = ""

    # Run best and write full reports with interpretation
    params_obj: dict[str, Any] = {}
    if best_params_json and best_params_json != "nan":
        try:
            parsed = json.loads(best_params_json)
            if isinstance(parsed, dict):
                params_obj = parsed
        except json.JSONDecodeError:
            params_obj = {}
    strat = StrategyRegistry.create(best_id, **params_obj)

    def provider(ohlcv: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
        return strat.build(ohlcv, cfg)

    engine = BacktestEngine(cfg, data, signal_provider=provider)
    result = engine.run()

    ctx = RunContext(
        data_source=f"Silver 일봉 ({root / 'data' / 'lake' / 'silver' / 'ohlcv_daily'})",
        start_date=args.start,
        end_date=args.end,
        symbols=tuple(str(s) for s in symbols[:50]) + (("...(+more)",) if len(symbols) > 50 else tuple()),
        n_trades=int(len(result.trade_log_df)),
        n_equity_days=int(len(result.equity_curve_df)),
    )
    interpretation = build_interpretation_ko(result.summary, ctx) + f"\n\n- **선택된 전략**: `{best_id}`\n"
    if best_params_json and best_params_json != "nan":
        interpretation += f"- **그리드 결과 베스트 파라미터(JSON)**: `{best_params_json}`\n"

    write_all_reports(
        result.trade_log_df,
        result.equity_curve_df,
        result.summary,
        out_dir,
        interpretation_md=interpretation,
    )

    def _to_md_table(frame: pd.DataFrame) -> str:
        cols = [c for c in frame.columns if c not in ("error",)]
        view = frame[cols].copy()
        # stringify
        for c in view.columns:
            view[c] = view[c].map(lambda x: "" if pd.isna(x) else str(x))
        headers = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
        rows_md = ["| " + " | ".join(map(str, r)) + " |" for r in view.itertuples(index=False, name=None)]
        return "\n".join(headers + rows_md)

    top = ok.sort_values("cagr", ascending=False).head(15) if ("cagr" in ok.columns and not ok.empty) else ok.head(15)
    md_lines = [
        "## Strategy Lab Ranking (by CAGR)",
        "",
        _to_md_table(top),
        "",
        f"- saved: `strategy_lab_summary.csv`",
    ]
    (out_dir / "strategy_lab_ranking.md").write_text("\n".join(md_lines), encoding="utf-8")
    if not ok.empty and "cagr" in ok.columns:
        ok.sort_values("cagr", ascending=False).to_csv(out_dir / "strategy_lab_ranking.csv", index=False)

    k_pool = max(1, int(args.top_k_per_strategy))
    top_pool = _top_k_per_strategy(df, k=k_pool)
    if not top_pool.empty:
        top_pool.to_csv(out_dir / "strategy_top3_per_strategy.csv", index=False)
        (out_dir / "strategy_top3_per_strategy.md").write_text(
            f"## Top-{k_pool} per strategy (by CAGR)\n\n" + _to_md_table(top_pool.head(120)),
            encoding="utf-8",
        )

    final_rank = final_ranking_from_top_pool(top_pool)
    if not final_rank.empty:
        final_rank.to_csv(out_dir / "strategy_lab_final_ranking.csv", index=False)
        (out_dir / "strategy_lab_final_ranking.md").write_text(
            "## Final ranking (pooled per-strategy Top-K → sort by CAGR)\n\n" + _to_md_table(final_rank.head(40)),
            encoding="utf-8",
        )

    if not args.no_html:
        try:
            write_strategy_lab_html(
                out_dir / "strategy_lab_report.html",
                title="Strategy Lab 리포트",
                best_strategy_id=best_id,
                best_params=params_obj,
                summary=result.summary,
                equity_curve_df=result.equity_curve_df,
                trade_log_df=result.trade_log_df,
                ohlcv_by_symbol=data,
                final_ranking_df=final_rank,
                max_symbol_charts=int(args.max_symbol_charts),
            )
        except ImportError as e:
            print(f"[warn] HTML skipped (install plotly): {e}", flush=True)
        else:
            print(f"HTML report: {out_dir / 'strategy_lab_report.html'}", flush=True)

    print(f"\nSaved: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

