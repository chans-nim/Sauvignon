"""In-memory broker with optional delayed fills and synthetic price stream."""

from __future__ import annotations

import logging
import uuid
from collections import deque
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import numpy as np
import pandas as pd

from broker_interface import BrokerInterface
from live_config import LiveTradingConfig
from models import MarketEvent, OrderRecord, OrderRequest, OrderSide, OrderStatus, OrderType

logger = logging.getLogger(__name__)


class MockBroker(BrokerInterface):
    """
    모의 브로커: 즉시 전량 체결(기본) 또는 ``poll_fills``에서 체결 이벤트를 반환한다.
    BUY 시 현금 검증, SELL 시 보유 수량 검증.
    """

    def __init__(
        self,
        config: LiveTradingConfig,
        initial_cash: float = 100_000_000.0,
        immediate_fill: bool = True,
        fill_delay_polls: int = 0,
        rng_seed: int | None = 42,
    ) -> None:
        self._config = config
        self._cash = float(initial_cash)
        self._positions: dict[str, int] = {s: 0 for s in config.symbols}
        self._last_prices: dict[str, float] = {
            s: 50_000.0 + i * 1_000.0 for i, s in enumerate(config.symbols)
        }
        self._connected = False
        self._orders: dict[str, OrderRecord] = {}
        self._fill_queue: deque[tuple[str, int]] = deque()
        self._immediate_fill = immediate_fill
        self._fill_delay_polls = max(0, fill_delay_polls)
        self._poll_counts: dict[str, int] = {}
        self._rng = np.random.default_rng(rng_seed)

    def connect(self) -> None:
        self._connected = True
        logger.info("MockBroker connected.")

    def disconnect(self) -> None:
        self._connected = False
        logger.info("MockBroker disconnected.")

    def is_connected(self) -> bool:
        return self._connected

    def get_cash_balance(self) -> float:
        return self._cash

    def get_positions(self) -> dict[str, Any]:
        return dict(self._positions)

    def _new_order_id(self) -> str:
        return f"MOCK-{uuid.uuid4().hex[:12].upper()}"

    def submit_order(self, order_request: OrderRequest) -> OrderRecord:
        if not self._connected:
            raise RuntimeError("MockBroker not connected")

        sym = order_request.symbol
        if sym not in self._positions:
            self._positions[sym] = 0

        oid = self._new_order_id()
        now = pd.Timestamp.utcnow()
        rec = OrderRecord(
            order_id=oid,
            symbol=order_request.symbol,
            side=order_request.side,
            qty=order_request.qty,
            filled_qty=0,
            order_type=order_request.order_type,
            requested_price=order_request.price,
            avg_fill_price=None,
            status=OrderStatus.ACKED,
            created_at=now,
            updated_at=now,
            reason=order_request.reason,
            broker_message="mock_ack",
        )
        self._orders[oid] = rec

        if order_request.side == OrderSide.BUY:
            px = float(order_request.price or self._last_prices.get(order_request.symbol, 50_000.0))
            est = px * order_request.qty * 1.001
            if est > self._cash:
                rej = replace(
                    rec,
                    status=OrderStatus.REJECTED,
                    broker_message="insufficient_cash",
                    updated_at=pd.Timestamp.utcnow(),
                )
                self._orders[oid] = rej
                logger.warning("Mock reject BUY %s: insufficient cash", order_request.symbol)
                return rej
        elif order_request.side == OrderSide.SELL:
            held = self._positions.get(order_request.symbol, 0)
            if order_request.qty > held:
                rej = replace(
                    rec,
                    status=OrderStatus.REJECTED,
                    broker_message="insufficient_position",
                    updated_at=pd.Timestamp.utcnow(),
                )
                self._orders[oid] = rej
                logger.warning("Mock reject SELL %s: qty %s > held %s", order_request.symbol, order_request.qty, held)
                return rej

        if self._immediate_fill and self._fill_delay_polls == 0:
            filled = self._apply_fill(oid)
            return self._orders[oid] if filled is None else filled

        self._fill_queue.append((oid, 0))
        self._poll_counts[oid] = 0
        logger.info("Mock order ACKED pending fill: %s %s %s", oid, order_request.side.value, order_request.symbol)
        return self._orders[oid]

    def _apply_fill(self, order_id: str) -> OrderRecord | None:
        rec = self._orders[order_id]
        if rec.status in (OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELED):
            return rec

        sym = rec.symbol
        px = float(rec.requested_price or self._last_prices.get(sym, 50_000.0))
        px = round(px, self._config.price_rounding_digits)
        qty = rec.qty

        if rec.side == OrderSide.BUY:
            cost = px * qty
            self._cash -= cost
            self._positions[sym] = self._positions.get(sym, 0) + qty
        else:
            proceeds = px * qty
            self._cash += proceeds
            self._positions[sym] = max(0, self._positions.get(sym, 0) - qty)

        filled = replace(
            rec,
            filled_qty=qty,
            avg_fill_price=px,
            status=OrderStatus.FILLED,
            updated_at=pd.Timestamp.utcnow(),
            broker_message="mock_filled",
        )
        self._orders[order_id] = filled
        self._last_prices[sym] = px
        logger.info("Mock FILL %s %s %s @ %s", rec.side.value, sym, qty, px)
        return filled

    def cancel_order(self, order_id: str) -> bool:
        rec = self._orders.get(order_id)
        if rec is None or rec.status != OrderStatus.ACKED:
            return False
        self._orders[order_id] = replace(
            rec,
            status=OrderStatus.CANCELED,
            updated_at=pd.Timestamp.utcnow(),
            broker_message="mock_canceled",
        )
        self._fill_queue = deque((x for x in self._fill_queue if x[0] != order_id))
        return True

    def get_order(self, order_id: str) -> OrderRecord | None:
        return self._orders.get(order_id)

    def list_open_orders(self) -> list[OrderRecord]:
        open_s = {OrderStatus.ACKED, OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED}
        return [o for o in self._orders.values() if o.status in open_s]

    def poll_fills(self) -> list[OrderRecord]:
        out: list[OrderRecord] = []
        pending = list(self._fill_queue)
        self._fill_queue.clear()
        for oid, _ in pending:
            rec = self._orders.get(oid)
            if rec is None or rec.status != OrderStatus.ACKED:
                continue
            cnt = self._poll_counts.get(oid, 0) + 1
            self._poll_counts[oid] = cnt
            if cnt > self._fill_delay_polls:
                f = self._apply_fill(oid)
                if f is not None:
                    out.append(f)
            else:
                self._fill_queue.append((oid, cnt))
        return out

    def set_mock_price(self, symbol: str, price: float) -> None:
        self._last_prices[symbol] = price

    def hydrate_ledger(self, cash: float, positions: dict[str, int]) -> None:
        """
        재시작 복구 시 스냅샷의 현금·보유수량을 모의 원장에 맞춘다.
        (``BrokerInterface``에는 없으며 Mock 전용.)
        """
        self._cash = float(cash)
        for sym, q in positions.items():
            self._positions[sym] = int(q)
        logger.info("MockBroker hydrate_ledger cash=%s positions=%s", self._cash, positions)

    def stream_market_data(self, symbols: list[str]) -> Iterator[MarketEvent]:
        """랜덤 워크 기반 틱 이벤트 제너레이터 (데모용, 무한에 가깝게 생성)."""
        t = pd.Timestamp.utcnow()
        step = 0
        while True:
            step += 1
            t = t + pd.Timedelta(seconds=int(self._config.poll_interval_seconds) or 1)
            for sym in symbols:
                base = max(100.0, float(self._last_prices.get(sym, 50_000.0)))
                shock = float(self._rng.normal(0, base * 0.0008))
                o = base
                c = max(100.0, base + shock)
                h = max(o, c) * (1.0 + abs(float(self._rng.uniform(0, 0.001))))
                l = min(o, c) * (1.0 - abs(float(self._rng.uniform(0, 0.001))))
                v = float(self._rng.integers(1_000, 50_000))
                self._last_prices[sym] = c
                yield MarketEvent(
                    symbol=sym,
                    event_time=t,
                    price=c,
                    volume=v,
                    open=o,
                    high=h,
                    low=l,
                    close=c,
                )
