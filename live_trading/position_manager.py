"""Live position state machine and fill application."""

from __future__ import annotations

import logging

import pandas as pd

from models import LivePosition, OrderRecord, OrderSide, OrderStatus, PositionStatus

logger = logging.getLogger(__name__)


class PositionManager:
    """심볼별 ``LivePosition`` 상태를 유지하고 체결로 갱신한다."""

    def __init__(self) -> None:
        self._positions: dict[str, LivePosition] = {}

    def load_positions(self, initial_positions: dict[str, LivePosition]) -> None:
        """스토어에서 복원한 포지션으로 덮어쓴다."""
        self._positions = dict(initial_positions)
        logger.info("load_positions count=%s", len(self._positions))

    def has_position(self, symbol: str) -> bool:
        p = self._positions.get(symbol)
        if p is None:
            return False
        return p.status in (PositionStatus.LONG, PositionStatus.PENDING_BUY, PositionStatus.PENDING_SELL, PositionStatus.EXITING)

    def get_position(self, symbol: str) -> LivePosition | None:
        return self._positions.get(symbol)

    def list_positions(self) -> dict[str, LivePosition]:
        return dict(self._positions)

    def _ensure_flat(self, symbol: str) -> LivePosition:
        if symbol not in self._positions:
            self._positions[symbol] = LivePosition(
                symbol=symbol,
                status=PositionStatus.FLAT,
                qty=0,
                avg_price=0.0,
                entry_time=None,
                highest_price=0.0,
                stop_price=None,
                last_signal_reason="",
                last_order_id=None,
                realized_pnl=0.0,
                unrealized_pnl=0.0,
            )
        return self._positions[symbol]

    def mark_pending_buy(self, symbol: str, order_id: str, reason: str) -> None:
        p = self._ensure_flat(symbol)
        p.status = PositionStatus.PENDING_BUY
        p.last_order_id = order_id
        p.last_signal_reason = reason
        logger.info("mark_pending_buy %s order=%s", symbol, order_id)

    def mark_pending_sell(self, symbol: str, order_id: str, reason: str) -> None:
        p = self._ensure_flat(symbol)
        p.status = PositionStatus.PENDING_SELL
        p.last_order_id = order_id
        p.last_signal_reason = reason
        logger.info("mark_pending_sell %s order=%s", symbol, order_id)

    def apply_fill(self, order_record: OrderRecord) -> float | None:
        """
        부분/전량 체결을 반영한다. BUY 시 LONG, SELL 완료 시 FLAT.
        매도 체결에 한해 해당 체결분 실현손익을 반환한다 (없으면 None).
        """
        if order_record.status not in (OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED):
            return None

        sym = order_record.symbol
        p = self._ensure_flat(sym)
        side = order_record.side
        fq = int(order_record.filled_qty)
        if fq <= 0 or order_record.avg_fill_price is None:
            return None
        px = float(order_record.avg_fill_price)

        if side == OrderSide.BUY:
            new_qty = p.qty + fq
            if new_qty <= 0:
                return None
            p.avg_price = (p.avg_price * p.qty + px * fq) / new_qty if new_qty else px
            p.qty = new_qty
            p.entry_time = p.entry_time or order_record.updated_at
            p.highest_price = max(p.highest_price, px)
            p.status = PositionStatus.LONG if order_record.status == OrderStatus.FILLED else PositionStatus.PENDING_BUY
            p.last_order_id = order_record.order_id
            logger.info("apply_fill BUY %s qty=%s avg=%s", sym, p.qty, p.avg_price)
            return None

        sell_qty = min(fq, p.qty)
        if sell_qty <= 0:
            return None
        pnl = (px - p.avg_price) * sell_qty
        p.realized_pnl += pnl
        p.qty -= sell_qty
        if p.qty <= 0 or order_record.status == OrderStatus.FILLED:
            p.qty = 0
            p.avg_price = 0.0
            p.entry_time = None
            p.highest_price = 0.0
            p.stop_price = None
            p.status = PositionStatus.FLAT
        else:
            p.status = PositionStatus.PENDING_SELL
        p.last_order_id = order_record.order_id
        logger.info("apply_fill SELL %s remaining=%s pnl_slice=%s", sym, p.qty, pnl)
        return float(pnl)

    def update_market_price(self, symbol: str, price: float) -> None:
        p = self._positions.get(symbol)
        if p is None or p.status != PositionStatus.LONG or p.qty <= 0:
            return
        p.unrealized_pnl = (price - p.avg_price) * p.qty

    def update_highest_price(self, symbol: str, price: float) -> None:
        p = self._positions.get(symbol)
        if p is None or p.status != PositionStatus.LONG:
            return
        p.highest_price = max(p.highest_price, price)

    def close_position(self, symbol: str) -> None:
        """메모리에서 제거하거나 FLAT으로 리셋한다."""
        if symbol in self._positions:
            self._positions[symbol] = LivePosition(
                symbol=symbol,
                status=PositionStatus.FLAT,
                qty=0,
                avg_price=0.0,
                entry_time=None,
                highest_price=0.0,
                stop_price=None,
                last_signal_reason="closed",
                last_order_id=None,
                realized_pnl=self._positions[symbol].realized_pnl,
                unrealized_pnl=0.0,
            )
        logger.info("close_position %s", symbol)

    def set_stop_after_entry(self, symbol: str, stop_price: float) -> None:
        p = self._positions.get(symbol)
        if p is None:
            return
        p.stop_price = stop_price
