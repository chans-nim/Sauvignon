"""Core enums and dataclasses for live trading."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

import pandas as pd


class PositionStatus(str, Enum):
    FLAT = "FLAT"
    PENDING_BUY = "PENDING_BUY"
    LONG = "LONG"
    PENDING_SELL = "PENDING_SELL"
    EXITING = "EXITING"
    ERROR = "ERROR"


class OrderStatus(str, Enum):
    CREATED = "CREATED"
    SUBMITTED = "SUBMITTED"
    ACKED = "ACKED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


def _ts_to_iso(ts: pd.Timestamp | None) -> str | None:
    if ts is None:
        return None
    return pd.Timestamp(ts).isoformat()


def _iso_to_ts(s: str | None) -> pd.Timestamp | None:
    if s is None:
        return None
    return pd.Timestamp(s)


@dataclass
class MarketEvent:
    symbol: str
    event_time: pd.Timestamp
    price: float
    volume: float | None
    open: float | None
    high: float | None
    low: float | None
    close: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "event_time": _ts_to_iso(self.event_time),
            "price": self.price,
            "volume": self.volume,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "MarketEvent":
        return MarketEvent(
            symbol=str(d["symbol"]),
            event_time=pd.Timestamp(d["event_time"]),
            price=float(d["price"]),
            volume=None if d.get("volume") is None else float(d["volume"]),
            open=None if d.get("open") is None else float(d["open"]),
            high=None if d.get("high") is None else float(d["high"]),
            low=None if d.get("low") is None else float(d["low"]),
            close=None if d.get("close") is None else float(d["close"]),
        )


@dataclass
class SignalDecision:
    symbol: str
    action: str
    score: float
    reason: str
    stop_price: float | None
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OrderRequest:
    symbol: str
    side: OrderSide
    qty: int
    order_type: OrderType
    price: float | None
    reason: str
    created_at: pd.Timestamp

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side.value,
            "qty": self.qty,
            "order_type": self.order_type.value,
            "price": self.price,
            "reason": self.reason,
            "created_at": _ts_to_iso(self.created_at),
        }


@dataclass
class OrderRecord:
    order_id: str
    symbol: str
    side: OrderSide
    qty: int
    filled_qty: int
    order_type: OrderType
    requested_price: float | None
    avg_fill_price: float | None
    status: OrderStatus
    created_at: pd.Timestamp
    updated_at: pd.Timestamp
    reason: str
    broker_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "qty": self.qty,
            "filled_qty": self.filled_qty,
            "order_type": self.order_type.value,
            "requested_price": self.requested_price,
            "avg_fill_price": self.avg_fill_price,
            "status": self.status.value,
            "created_at": _ts_to_iso(self.created_at),
            "updated_at": _ts_to_iso(self.updated_at),
            "reason": self.reason,
            "broker_message": self.broker_message,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "OrderRecord":
        return OrderRecord(
            order_id=str(d["order_id"]),
            symbol=str(d["symbol"]),
            side=OrderSide(str(d["side"])),
            qty=int(d["qty"]),
            filled_qty=int(d.get("filled_qty", 0)),
            order_type=OrderType(str(d["order_type"])),
            requested_price=None
            if d.get("requested_price") is None
            else float(d["requested_price"]),
            avg_fill_price=None
            if d.get("avg_fill_price") is None
            else float(d["avg_fill_price"]),
            status=OrderStatus(str(d["status"])),
            created_at=_iso_to_ts(d.get("created_at")) or pd.Timestamp.utcnow(),
            updated_at=_iso_to_ts(d.get("updated_at")) or pd.Timestamp.utcnow(),
            reason=str(d.get("reason", "")),
            broker_message=str(d.get("broker_message", "")),
        )


@dataclass
class LivePosition:
    symbol: str
    status: PositionStatus
    qty: int
    avg_price: float
    entry_time: pd.Timestamp | None
    highest_price: float
    stop_price: float | None
    last_signal_reason: str
    last_order_id: str | None
    realized_pnl: float
    unrealized_pnl: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "status": self.status.value,
            "qty": self.qty,
            "avg_price": self.avg_price,
            "entry_time": _ts_to_iso(self.entry_time),
            "highest_price": self.highest_price,
            "stop_price": self.stop_price,
            "last_signal_reason": self.last_signal_reason,
            "last_order_id": self.last_order_id,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "LivePosition":
        return LivePosition(
            symbol=str(d["symbol"]),
            status=PositionStatus(str(d["status"])),
            qty=int(d["qty"]),
            avg_price=float(d["avg_price"]),
            entry_time=_iso_to_ts(d.get("entry_time")),
            highest_price=float(d["highest_price"]),
            stop_price=None if d.get("stop_price") is None else float(d["stop_price"]),
            last_signal_reason=str(d.get("last_signal_reason", "")),
            last_order_id=d.get("last_order_id"),
            realized_pnl=float(d.get("realized_pnl", 0.0)),
            unrealized_pnl=float(d.get("unrealized_pnl", 0.0)),
        )
