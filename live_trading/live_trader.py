"""Orchestrates market events, signals, risk, orders, positions, and persistence."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from broker_interface import BrokerInterface
from live_config import LiveTradingConfig
from market_data_handler import MarketDataHandler
from models import (
    LivePosition,
    MarketEvent,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionStatus,
)
from order_manager import OrderManager
from position_manager import PositionManager
from quote_utils import last_bar_return_pct
from risk_manager import RiskManager
from signal_adapter import SignalAdapter
from state_store import JSONStateStore

logger = logging.getLogger(__name__)


class LiveTrader:
    """
    실시간 루프: 시세 갱신 → (필요 시) 체결 반영 → 청산 우선 → 진입 → 주기적 상태 저장.
    """

    def __init__(
        self,
        config: LiveTradingConfig,
        broker: BrokerInterface,
        signal_adapter: SignalAdapter,
        market_data_handler: MarketDataHandler,
        order_manager: OrderManager,
        position_manager: PositionManager,
        risk_manager: RiskManager,
        state_store: JSONStateStore,
        logger_: logging.Logger | None = None,
    ) -> None:
        self._cfg = config
        self._broker = broker
        self._signals = signal_adapter
        self._md = market_data_handler
        self._orders = order_manager
        self._pos = position_manager
        self._risk = risk_manager
        self._store = state_store
        self._log = logger_ or logger
        self._daily_stats: dict[str, Any] = {}
        self._event_count = 0

    def initialize(self) -> None:
        """브로커 연결 및 일일 통계 초기화."""
        self._broker.connect()
        self._daily_stats = {
            "trading_date": None,
            "new_entries": 0,
            "blocked_by_loss": False,
            "last_exit_ts": {},
            "start_equity": None,
            "consecutive_losing_entries": 0,
        }
        self._log.info("LiveTrader initialized mode=%s symbols=%s", self._cfg.mode, self._cfg.symbols)

    def restore_state(self) -> None:
        """JSON 스토어에서 포지션·일일 통계 복원."""
        snap = self._store.load_snapshot()
        loaded_pos = self._store.load_positions()
        if loaded_pos:
            self._pos.load_positions(loaded_pos)
            self._log.info("State restore: %s positions", len(loaded_pos))
        if snap and hasattr(self._broker, "hydrate_ledger"):
            cash = float(snap.get("cash", self._broker.get_cash_balance()))
            ledger = {
                s: p.qty
                for s, p in self._pos.list_positions().items()
                if p.qty > 0
                and p.status
                in (
                    PositionStatus.LONG,
                    PositionStatus.PENDING_SELL,
                    PositionStatus.EXITING,
                )
            }
            self._broker.hydrate_ledger(cash, ledger)  # type: ignore[attr-defined]
            self._log.info("Broker ledger hydrated from snapshot (long symbols=%s)", len(ledger))
        ds = self._store.load_daily_stats()
        if ds:
            self._daily_stats.update(ds)
            self._log.info("Daily stats restored keys=%s", list(ds.keys()))

    def _update_daily_roll(self, event_time: pd.Timestamp) -> None:
        local = pd.Timestamp(event_time).tz_localize("UTC") if pd.Timestamp(event_time).tzinfo is None else event_time
        local = pd.Timestamp(local).tz_convert(self._cfg.timezone)
        day = local.date().isoformat()
        if self._daily_stats.get("trading_date") != day:
            self._daily_stats["trading_date"] = day
            self._daily_stats["new_entries"] = 0
            self._daily_stats["blocked_by_loss"] = False
            self._daily_stats["start_equity"] = None
            self._daily_stats["consecutive_losing_entries"] = 0
            self._log.info("Rolled daily stats to %s", day)

    def _current_equity(self) -> float:
        cash = float(self._broker.get_cash_balance())
        mv = 0.0
        for sym, p in self._pos.list_positions().items():
            if p.status == PositionStatus.LONG and p.qty > 0:
                px = self._md.get_latest_price(sym)
                if px is not None:
                    mv += px * p.qty
        return cash + mv

    def _update_loss_guard(self) -> None:
        eq = self._current_equity()
        if self._daily_stats.get("start_equity") is None:
            self._daily_stats["start_equity"] = eq
        start = float(self._daily_stats["start_equity"])
        limit = float(self._cfg.daily_loss_limit)
        if limit > 0 and (eq - start) <= -limit:
            self._daily_stats["blocked_by_loss"] = True
            self._log.warning("Daily loss limit hit: start=%s now=%s limit=%s", start, eq, limit)

    def _register_sell_pnl(self, sell_pnl: float | None) -> None:
        """매도 체결 손익 구간으로 연속 손실 카운터를 갱신한다."""
        if sell_pnl is None:
            return
        lim = int(self._cfg.max_consecutive_losing_entries)
        if lim <= 0:
            return
        key = "consecutive_losing_entries"
        if sell_pnl < 0:
            self._daily_stats[key] = int(self._daily_stats.get(key, 0)) + 1
            self._log.info("Consecutive losing fills -> %s", self._daily_stats[key])
        else:
            self._daily_stats[key] = 0

    def handle_market_event(self, event: MarketEvent) -> None:
        """단일 시세 이벤트 처리."""
        self._update_daily_roll(event.event_time)
        self._md.update_event(event)
        self.process_order_updates()

        if self._risk.should_force_exit(event.event_time):
            self._force_exit_all(event.event_time)

        self.evaluate_symbol(event.symbol, event.event_time)
        self._update_loss_guard()
        self._event_count += 1
        if self._event_count % 25 == 0:
            self.persist_state()

    def evaluate_symbol(self, symbol: str, event_time: pd.Timestamp) -> None:
        px = self._md.get_latest_price(symbol)
        if px is not None:
            self._pos.update_market_price(symbol, px)
            self._pos.update_highest_price(symbol, px)
        self.try_exit(symbol, event_time)
        self.try_enter(symbol, event_time)

    def try_exit(self, symbol: str, event_time: pd.Timestamp) -> None:
        p = self._pos.get_position(symbol)
        if p is None or p.status != PositionStatus.LONG or p.qty <= 0:
            return
        frame = self._md.get_symbol_frame(symbol)
        dec = self._signals.evaluate_exit(p, frame)
        if dec.action != "SELL":
            return
        if not self._orders.can_submit(symbol, OrderSide.SELL):
            return
        ok, reason = self._risk.can_place_new_orders(event_time)
        if not ok:
            self._log.debug("try_exit blocked: %s", reason)
            return

        px_out = self._md.get_latest_price(symbol)
        req = OrderRequest(
            symbol=symbol,
            side=OrderSide.SELL,
            qty=p.qty,
            order_type=OrderType.MARKET,
            price=px_out,
            reason=dec.reason,
            created_at=pd.Timestamp(event_time),
        )
        self._pos.mark_pending_sell(symbol, "pending", dec.reason)
        rec = self._orders.submit(req)
        if rec.status == OrderStatus.REJECTED:
            self._log.warning("Exit order rejected %s %s", symbol, rec.broker_message)
            return
        self._pos.mark_pending_sell(symbol, rec.order_id, dec.reason)
        if rec.status == OrderStatus.FILLED:
            pnl = self._pos.apply_fill(rec)
            self._register_sell_pnl(pnl)
            self._orders.mark_order_closed(rec.order_id)
            ts = pd.Timestamp(event_time)
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC").tz_convert(self._cfg.timezone)
            else:
                ts = ts.tz_convert(self._cfg.timezone)
            self._daily_stats.setdefault("last_exit_ts", {})[symbol] = ts.isoformat()
            self._log.info("Signal exit filled %s reason=%s", symbol, dec.reason)

    def try_enter(self, symbol: str, event_time: pd.Timestamp) -> None:
        ok, reason = self._risk.can_place_new_orders(event_time)
        if not ok:
            return
        p = self._pos.get_position(symbol)
        has_pos = p is not None and p.status != PositionStatus.FLAT
        frame = self._md.get_symbol_frame(symbol)
        dec = self._signals.evaluate_entry(symbol, frame, has_position=has_pos)
        if dec.action != "BUY":
            return
        px = self._md.get_latest_price(symbol)
        if px is None or px <= 0:
            return
        qty = int(self._cfg.allocation_per_trade // px)
        if qty <= 0:
            return

        cash = float(self._broker.get_cash_balance())
        bar_ret = last_bar_return_pct(frame)
        allow, msg = self._risk.can_enter(
            symbol,
            event_time,
            px,
            self._pos.list_positions(),
            cash,
            self._orders.active_orders,
            self._daily_stats,
            last_bar_return_pct=bar_ret,
        )
        if not allow:
            self._log.debug("try_enter denied %s: %s", symbol, msg)
            return
        if not self._orders.can_submit(symbol, OrderSide.BUY):
            return

        req = OrderRequest(
            symbol=symbol,
            side=OrderSide.BUY,
            qty=qty,
            order_type=OrderType.MARKET,
            price=px,
            reason=dec.reason,
            created_at=pd.Timestamp(event_time),
        )
        self._pos.mark_pending_buy(symbol, "pending", dec.reason)
        rec = self._orders.submit(req)
        if rec.status == OrderStatus.REJECTED:
            self._log.warning("Entry order rejected %s %s", symbol, rec.broker_message)
            return
        self._pos.mark_pending_buy(symbol, rec.order_id, dec.reason)
        if rec.status == OrderStatus.FILLED:
            self._pos.apply_fill(rec)
            if dec.stop_price is not None:
                self._pos.set_stop_after_entry(symbol, float(dec.stop_price))
            self._orders.mark_order_closed(rec.order_id)
            self._daily_stats["new_entries"] = int(self._daily_stats.get("new_entries", 0)) + 1
            self._log.info(
                "Entry filled %s qty=%s score=%s stop=%s",
                symbol,
                qty,
                dec.score,
                dec.stop_price,
            )

    def process_order_updates(self) -> None:
        """브로커 체결 폴링 후 포지션 반영."""
        for rec in self._orders.process_fills():
            pnl = self._pos.apply_fill(rec)
            if rec.status == OrderStatus.FILLED:
                if rec.side == OrderSide.BUY:
                    p = self._pos.get_position(rec.symbol)
                    if p is not None and p.avg_price > 0:
                        sp = p.avg_price * (1.0 - self._cfg.stop_loss_pct)
                        self._pos.set_stop_after_entry(rec.symbol, sp)
                    self._daily_stats["new_entries"] = int(self._daily_stats.get("new_entries", 0)) + 1
                if rec.side == OrderSide.SELL:
                    self._register_sell_pnl(pnl)
                    ts = pd.Timestamp.utcnow().tz_localize("UTC").tz_convert(self._cfg.timezone)
                    self._daily_stats.setdefault("last_exit_ts", {})[rec.symbol] = ts.isoformat()
                self._orders.mark_order_closed(rec.order_id)

    def persist_state(self) -> None:
        self._store.save_positions(self._pos.list_positions())
        self._store.save_orders(self._orders.active_orders)
        self._store.save_daily_stats(self._daily_stats)
        snap = {
            "event_count": self._event_count,
            "cash": self._broker.get_cash_balance(),
            "positions": {k: v.to_dict() for k, v in self._pos.list_positions().items()},
        }
        self._store.save_snapshot(snap)
        self._log.debug("persist_state event_count=%s", self._event_count)

    def _force_exit_all(self, event_time: pd.Timestamp) -> None:
        for sym, p in list(self._pos.list_positions().items()):
            if p.status == PositionStatus.LONG and p.qty > 0:
                self.try_exit(sym, event_time)

    def run(self, max_events: int | None = None) -> None:
        """``stream_market_data``를 순회하며 이벤트를 처리한다."""
        self.initialize()
        self.restore_state()
        n = 0
        try:
            for raw in self._broker.stream_market_data(self._cfg.symbols):
                if isinstance(raw, MarketEvent):
                    ev = raw
                else:
                    raise TypeError(f"Unexpected stream payload: {type(raw)}")
                self.handle_market_event(ev)
                n += 1
                if max_events is not None and n >= max_events:
                    break
        finally:
            self.persist_state()
            self.shutdown()

    def shutdown(self) -> None:
        self.persist_state()
        self._broker.disconnect()
        self._log.info("LiveTrader shutdown complete events=%s", self._event_count)

    @property
    def event_count(self) -> int:
        """처리한 시세 이벤트 수."""
        return self._event_count

    def read_last_snapshot(self) -> dict[str, Any]:
        """저장된 스냅샷(JSON)을 로드한다."""
        return self._store.load_snapshot()

    def cash_balance(self) -> float:
        """브로커 현금 잔고."""
        return float(self._broker.get_cash_balance())

    def list_positions_public(self) -> dict[str, LivePosition]:
        """포지션 맵의 얕은 복사 (데모/모니터링용)."""
        return self._pos.list_positions()
