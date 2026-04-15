"""Portfolio, positions, and record types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any

import pandas as pd


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class Position:
    symbol: str
    quantity: int
    avg_entry_price: float
    entry_date: date
    stop_price: float
    highest_high_since_entry: float


@dataclass
class TradeRecord:
    trade_id: int
    date: date
    symbol: str
    side: Side
    quantity: int
    price: float
    commission: float
    tax: float
    reason: str


@dataclass
class EquityRecord:
    date: date
    cash: float
    market_value: float
    equity: float


@dataclass
class Portfolio:
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[TradeRecord] = field(default_factory=list)
    equity_history: list[EquityRecord] = field(default_factory=list)
    _next_trade_id: int = 1

    def has_position(self, symbol: str) -> bool:
        return symbol in self.positions

    def open_slots(self, max_positions: int) -> int:
        return max(0, max_positions - len(self.positions))

    def add_trade(self, rec: TradeRecord) -> None:
        self.trades.append(rec)

    def next_trade_id(self) -> int:
        tid = self._next_trade_id
        self._next_trade_id += 1
        return tid

    def record_equity(self, as_of: date, market_value: float) -> None:
        equity = self.cash + market_value
        self.equity_history.append(
            EquityRecord(
                date=as_of,
                cash=self.cash,
                market_value=market_value,
                equity=equity,
            )
        )


def trades_to_dataframe(trades: list[TradeRecord]) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame(
            columns=[
                "trade_id",
                "date",
                "symbol",
                "side",
                "quantity",
                "price",
                "commission",
                "tax",
                "reason",
            ]
        )
    rows: list[dict[str, Any]] = []
    for t in trades:
        rows.append(
            {
                "trade_id": t.trade_id,
                "date": t.date,
                "symbol": t.symbol,
                "side": t.side.value,
                "quantity": t.quantity,
                "price": t.price,
                "commission": t.commission,
                "tax": t.tax,
                "reason": t.reason,
            }
        )
    return pd.DataFrame(rows)


def equity_to_dataframe(history: list[EquityRecord]) -> pd.DataFrame:
    if not history:
        return pd.DataFrame(columns=["date", "cash", "market_value", "equity"])
    rows = [
        {
            "date": e.date,
            "cash": e.cash,
            "market_value": e.market_value,
            "equity": e.equity,
        }
        for e in history
    ]
    return pd.DataFrame(rows)
