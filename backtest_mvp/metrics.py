"""Performance metrics from equity curve and trade log."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PerformanceSummary:
    total_return: float
    cagr: float
    mdd: float
    win_rate: float
    profit_factor: float
    avg_gain: float
    avg_loss: float
    avg_holding_days: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_return": self.total_return,
            "cagr": self.cagr,
            "mdd": self.mdd,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "avg_gain": self.avg_gain,
            "avg_loss": self.avg_loss,
            "avg_holding_days": self.avg_holding_days,
        }


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = (equity / peak) - 1.0
    return float(dd.min())


def _cagr(equity: pd.Series, dates: pd.Series) -> float:
    if len(equity) < 2:
        return 0.0
    start_v = float(equity.iloc[0])
    end_v = float(equity.iloc[-1])
    if start_v <= 0:
        return 0.0
    start = pd.Timestamp(dates.iloc[0])
    end = pd.Timestamp(dates.iloc[-1])
    years = (end - start).days / 365.25
    if years <= 0:
        return 0.0
    return float((end_v / start_v) ** (1.0 / years) - 1.0)


def compute_metrics(
    equity_curve_df: pd.DataFrame,
    trade_log_df: pd.DataFrame,
) -> PerformanceSummary:
    eq = equity_curve_df.sort_values("date").reset_index(drop=True)
    if eq.empty:
        return PerformanceSummary(
            total_return=0.0,
            cagr=0.0,
            mdd=0.0,
            win_rate=0.0,
            profit_factor=0.0,
            avg_gain=0.0,
            avg_loss=0.0,
            avg_holding_days=0.0,
        )

    e = eq["equity"].astype(float)
    total_return = float(e.iloc[-1] / e.iloc[0] - 1.0) if e.iloc[0] > 0 else 0.0
    mdd = _max_drawdown(e)
    cagr = _cagr(e, eq["date"])

    win_rate = 0.0
    profit_factor = 0.0
    avg_gain = 0.0
    avg_loss = 0.0
    avg_holding_days = 0.0

    if not trade_log_df.empty:
        td = trade_log_df.sort_values(["symbol", "date", "trade_id"]).copy()
        rounds = _pair_trades_to_rounds(td)
        if rounds:
            pnls = [r["pnl"] for r in rounds]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p < 0]
            win_rate = len(wins) / len(pnls) if pnls else 0.0
            gross_win = sum(wins)
            gross_loss = -sum(losses)
            profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0
            if profit_factor == float("inf"):
                profit_factor = 999.99
            avg_gain = float(np.mean(wins)) if wins else 0.0
            avg_loss = float(np.mean(losses)) if losses else 0.0
            holds = [r["hold_days"] for r in rounds]
            avg_holding_days = float(np.mean(holds)) if holds else 0.0

    return PerformanceSummary(
        total_return=total_return,
        cagr=cagr,
        mdd=mdd,
        win_rate=win_rate,
        profit_factor=profit_factor,
        avg_gain=avg_gain,
        avg_loss=avg_loss,
        avg_holding_days=avg_holding_days,
    )


def _pair_trades_to_rounds(trades: pd.DataFrame) -> list[dict[str, Any]]:
    """FIFO per symbol: match BUY then SELL to compute round-trip PnL and holding days."""
    rounds: list[dict[str, Any]] = []
    for sym in trades["symbol"].unique():
        sub = trades[trades["symbol"] == sym]
        fifo_qty = 0
        fifo_cost = 0.0
        buy_date: pd.Timestamp | None = None
        for _, row in sub.iterrows():
            side = str(row["side"]).upper()
            q = int(row["quantity"])
            px = float(row["price"])
            comm = float(row["commission"])
            tax = float(row["tax"])
            d = pd.Timestamp(row["date"])
            if side == "BUY":
                fifo_qty += q
                fifo_cost += q * px + comm + tax
                if buy_date is None:
                    buy_date = d
            elif side == "SELL" and fifo_qty > 0:
                take = min(q, fifo_qty)
                avg_cost = fifo_cost / fifo_qty
                proceeds = take * px - comm - tax
                pnl = proceeds - take * avg_cost
                fifo_cost -= take * avg_cost
                fifo_qty -= take
                hold_days = (d - buy_date).days if buy_date is not None else 0
                rounds.append({"pnl": pnl, "hold_days": max(0, hold_days)})
                if fifo_qty == 0:
                    buy_date = None
    return rounds
