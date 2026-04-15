"""Persist backtest outputs and print summary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from metrics import PerformanceSummary


def save_trade_log_csv(trade_log_df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    trade_log_df.to_csv(path, index=False)


def save_equity_curve_csv(equity_curve_df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    equity_curve_df.to_csv(path, index=False)


def save_summary_json(summary: PerformanceSummary, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(summary.to_dict(), f, indent=2, ensure_ascii=False)


def save_interpretation_md(text: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def print_summary(summary: PerformanceSummary, paths: dict[str, Path]) -> None:
    d = summary.to_dict()
    lines = [
        "",
        "=== Backtest Summary ===",
        f"Total return:     {d['total_return']:.2%}",
        f"CAGR:             {d['cagr']:.2%}",
        f"Max drawdown:     {d['mdd']:.2%}",
        f"Win rate:         {d['win_rate']:.2%}",
        f"Profit factor:    {d['profit_factor']:.3f}",
        f"Avg gain (win):   {d['avg_gain']:,.0f}",
        f"Avg loss:         {d['avg_loss']:,.0f}",
        f"Avg hold (days):  {d['avg_holding_days']:.2f}",
        "",
        "Output files:",
    ]
    for k, p in paths.items():
        lines.append(f"  {k}: {p}")
    print("\n".join(lines))


def write_all_reports(
    trade_log_df: pd.DataFrame,
    equity_curve_df: pd.DataFrame,
    summary: PerformanceSummary,
    out_dir: Path,
    *,
    interpretation_md: str | None = None,
) -> dict[str, Path]:
    paths = {
        "trade_log": out_dir / "trade_log.csv",
        "equity_curve": out_dir / "equity_curve.csv",
        "summary": out_dir / "summary.json",
    }
    save_trade_log_csv(trade_log_df, paths["trade_log"])
    save_equity_curve_csv(equity_curve_df, paths["equity_curve"])
    save_summary_json(summary, paths["summary"])
    if interpretation_md is not None:
        ip = out_dir / "interpretation.md"
        save_interpretation_md(interpretation_md, ip)
        paths["interpretation"] = ip
    print_summary(summary, paths)
    if interpretation_md is not None:
        print("\n=== 결과 해석 (interpretation.md) ===\n")
        print(interpretation_md)
    return paths
