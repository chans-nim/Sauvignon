"""Pre-trade risk checks and session guards (close cutoff, loss limit)."""

from __future__ import annotations

import logging
import pandas as pd

from live_config import LiveTradingConfig, _parse_hhmm
from models import LivePosition, OrderRecord, PositionStatus

logger = logging.getLogger(__name__)


class RiskManager:
    """진입 전 검증: 보유 한도, 현금, 일일 진입 한도, 쿨다운, 장 마감 근접, 손실 한도, 활성 주문."""

    def __init__(self, config: LiveTradingConfig, logger_: logging.Logger | None = None) -> None:
        self._cfg = config
        self._log = logger_ or logger

    def _localize(self, now: pd.Timestamp) -> pd.Timestamp:
        ts = pd.Timestamp(now)
        if ts.tzinfo is None:
            return ts.tz_localize("UTC").tz_convert(self._cfg.timezone)
        return ts.tz_convert(self._cfg.timezone)

    def _time_today(self, now: pd.Timestamp, hhmm: str) -> pd.Timestamp:
        local = self._localize(now)
        h, m = _parse_hhmm(hhmm)
        base = local.normalize()
        return base + pd.Timedelta(hours=h, minutes=m)

    def _count_long_positions(self, current_positions: dict[str, LivePosition]) -> int:
        n = 0
        for p in current_positions.values():
            if p.status == PositionStatus.LONG and p.qty > 0:
                n += 1
        return n

    def _has_active_order_for_symbol(self, symbol: str, active_orders: dict[str, OrderRecord]) -> bool:
        for rec in active_orders.values():
            if rec.symbol != symbol:
                continue
            if rec.status.name in ("FILLED", "CANCELED", "REJECTED", "FAILED"):
                continue
            return True
        return False

    def can_enter(
        self,
        symbol: str,
        now: pd.Timestamp,
        price: float,
        current_positions: dict[str, LivePosition],
        cash: float,
        active_orders: dict[str, OrderRecord],
        daily_stats: dict[str, object],
        *,
        last_bar_return_pct: float | None = None,
    ) -> tuple[bool, str]:
        if price <= 0:
            return False, "invalid_price"
        if daily_stats.get("blocked_by_loss"):
            return False, "daily_loss_limit"

        lim = self._cfg.max_consecutive_losing_entries
        if lim > 0 and int(daily_stats.get("consecutive_losing_entries", 0)) >= lim:
            return False, "consecutive_losing_entries"

        longs = self._count_long_positions(current_positions)
        if longs >= self._cfg.max_positions:
            return False, "max_positions"

        sector = self._cfg.symbol_sector.get(symbol, "__default__")
        caps = self._cfg.max_positions_per_sector
        if caps:
            cap = caps.get(sector)
            if cap is not None:
                n_sec = 0
                for s, pos in current_positions.items():
                    if pos.status == PositionStatus.LONG and pos.qty > 0:
                        if self._cfg.symbol_sector.get(s, "__default__") == sector:
                            n_sec += 1
                if n_sec >= cap:
                    return False, "sector_position_cap"

        p = current_positions.get(symbol)
        if p is not None and p.status != PositionStatus.FLAT:
            return False, "symbol_not_flat"

        if self._has_active_order_for_symbol(symbol, active_orders):
            return False, "active_order_exists"

        need = float(self._cfg.allocation_per_trade)
        est_qty = int(need // price) if price > 0 else 0
        if est_qty <= 0:
            return False, "qty_zero"
        if cash < need * 0.99:
            return False, "insufficient_cash"

        entries = int(daily_stats.get("new_entries", 0))
        if entries >= self._cfg.max_daily_new_entries:
            return False, "max_daily_new_entries"

        local = self._localize(now)

        if self._cfg.no_entry_first_minutes_after_open > 0:
            open_t = self._time_today(now, self._cfg.market_open_time)
            minutes_after_open = (local - open_t).total_seconds() / 60.0
            if minutes_after_open >= 0 and minutes_after_open < float(self._cfg.no_entry_first_minutes_after_open):
                return False, "post_open_volatility_window"

        chase = float(self._cfg.chase_bar_return_limit_pct)
        if chase > 0 and last_bar_return_pct is not None and last_bar_return_pct > chase:
            return False, "chase_momentum_limit"

        cutoff = self._time_today(now, self._cfg.no_new_entry_after)
        if local >= cutoff:
            return False, "no_new_entry_after_cutoff"

        last_exit_raw = daily_stats.get("last_exit_ts", {})
        if isinstance(last_exit_raw, dict) and symbol in last_exit_raw:
            raw_ts = last_exit_raw[symbol]
            try:
                last_exit = pd.Timestamp(raw_ts)
                if last_exit.tzinfo is None:
                    last_exit = last_exit.tz_localize(self._cfg.timezone)
                else:
                    last_exit = last_exit.tz_convert(self._cfg.timezone)
                delta_min = (local - last_exit).total_seconds() / 60.0
                if delta_min < self._cfg.reentry_cooldown_minutes:
                    return False, "reentry_cooldown"
            except Exception as e:
                self._log.warning("last_exit_ts parse failed for %s: %s", symbol, e)

        return True, "ok"

    def should_force_exit(self, now: pd.Timestamp) -> bool:
        """강제 청산 시각 도달 여부 (옵션)."""
        if not self._cfg.force_exit_before_close:
            return False
        local = self._localize(now)
        t_exit = self._time_today(now, self._cfg.force_exit_time)
        return local >= t_exit

    def can_place_new_orders(self, now: pd.Timestamp) -> tuple[bool, str]:
        """일일 손실 한도 등으로 모든 신규 주문을 중단해야 하는지."""
        # live_trader가 daily_stats['blocked_by_loss']를 세팅 — 여기서는 장 종료 직전만 보조 검사
        local = self._localize(now)
        close_t = self._time_today(now, self._cfg.market_close_time)
        if local >= close_t:
            return False, "market_closed"
        return True, "ok"
