"""Portfolio backtest orchestration: signals vs execution separated."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, Mapping

import pandas as pd

from config import BacktestConfig
from execution_model import buy_at_open, sell_at_open, sell_stop_loss
from metrics import PerformanceSummary, compute_metrics
from portfolio import Portfolio, Position, Side, TradeRecord, equity_to_dataframe, trades_to_dataframe
from signals import SignalFrame, build_signals


@dataclass
class BacktestResult:
    trade_log_df: pd.DataFrame
    equity_curve_df: pd.DataFrame
    summary: PerformanceSummary


class BacktestEngine:
    def __init__(
        self,
        config: BacktestConfig,
        ohlcv_by_symbol: Mapping[str, pd.DataFrame],
        *,
        signal_provider: Callable[[pd.DataFrame, BacktestConfig], pd.DataFrame] | None = None,
    ) -> None:
        config.validate()
        self.cfg = config
        self.ohlcv_by_symbol = {k: v.sort_index() for k, v in ohlcv_by_symbol.items()}
        self._signal_provider = signal_provider or (lambda ohlcv, cfg: build_signals(ohlcv, cfg).df)
        self._signals: dict[str, SignalFrame] = {}
        for sym, df in self.ohlcv_by_symbol.items():
            sdf = self._signal_provider(df, config)
            for col in ("entry_signal", "exit_ma20_signal", "score_composite"):
                if col not in sdf.columns:
                    raise ValueError(f"signal_provider must provide column={col!r} for symbol={sym}")
            self._signals[sym] = SignalFrame(df=sdf)

    def _calendar(self) -> list[pd.Timestamp]:
        # Union calendar: allows large universes without shrinking to tiny intersection.
        all_dates: set[pd.Timestamp] = set()
        for df in self.ohlcv_by_symbol.values():
            all_dates.update(df.index)
        return sorted(all_dates)

    def _bar_optional(self, symbol: str, d: pd.Timestamp) -> pd.Series | None:
        df = self.ohlcv_by_symbol[symbol]
        if d not in df.index:
            return None
        return df.loc[d]

    def _last_close_on_or_before(self, symbol: str, d: pd.Timestamp) -> float | None:
        df = self.ohlcv_by_symbol[symbol]
        if df.empty:
            return None
        # fast path
        if d in df.index:
            return float(df.loc[d]["close"])
        idx = df.index
        pos = idx.searchsorted(d, side="right") - 1
        if pos < 0:
            return None
        return float(df.iloc[pos]["close"])

    def _prev_date(self, cal: list[pd.Timestamp], d: pd.Timestamp) -> pd.Timestamp | None:
        i = cal.index(d)
        return cal[i - 1] if i > 0 else None

    def _market_value(self, as_of: pd.Timestamp) -> float:
        mv = 0.0
        for sym, pos in self.portfolio.positions.items():
            c = self._last_close_on_or_before(sym, as_of)
            if c is None:
                continue
            mv += c * pos.quantity
        return mv

    def _process_stop_exits(self, d: pd.Timestamp) -> None:
        """Intraday stop / trailing using open & low (갭하락 포함)."""
        for sym in list(self.portfolio.positions.keys()):
            row = self._bar_optional(sym, d)
            if row is None:
                continue
            o, h, low = float(row["open"]), float(row["high"]), float(row["low"])
            pos = self.portfolio.positions[sym]
            hh = max(pos.highest_high_since_entry, h)
            trail_level = hh * (1.0 - self.cfg.trailing_stop_pct)
            eff_stop = max(pos.stop_price, trail_level)
            fill = sell_stop_loss(o, low, eff_stop, pos.quantity, self.cfg)
            if fill is None:
                continue
            tid = self.portfolio.next_trade_id()
            net = fill.gross - fill.commission - fill.tax
            self.portfolio.cash += net
            del self.portfolio.positions[sym]
            self.portfolio.add_trade(
                TradeRecord(
                    trade_id=tid,
                    date=d.date(),
                    symbol=sym,
                    side=Side.SELL,
                    quantity=fill.quantity,
                    price=fill.price,
                    commission=fill.commission,
                    tax=fill.tax,
                    reason="STOP_OR_TRAILING",
                )
            )

    def _process_ma20_open_exits(self, d: pd.Timestamp, prev: pd.Timestamp) -> None:
        """전일 종가 기준 MA20 이탈 신호 -> 당일 시가 청산 (진입과 동일한 타이밍 규칙)."""
        for sym in list(self.portfolio.positions.keys()):
            sdf = self._signals[sym].df
            if prev not in sdf.index:
                continue
            prev_row = sdf.loc[prev]
            if not bool(prev_row["exit_ma20_signal"]):
                continue
            pos = self.portfolio.positions[sym]
            row = self._bar_optional(sym, d)
            if row is None:
                continue
            open_px = float(row["open"])
            fill = sell_at_open(open_px, pos.quantity, self.cfg)
            if fill is None:
                continue
            tid = self.portfolio.next_trade_id()
            net = fill.gross - fill.commission - fill.tax
            self.portfolio.cash += net
            del self.portfolio.positions[sym]
            self.portfolio.add_trade(
                TradeRecord(
                    trade_id=tid,
                    date=d.date(),
                    symbol=sym,
                    side=Side.SELL,
                    quantity=fill.quantity,
                    price=fill.price,
                    commission=fill.commission,
                    tax=fill.tax,
                    reason="MA20_EXIT_OPEN",
                )
            )

    def _process_entries(self, d: pd.Timestamp, prev: pd.Timestamp) -> None:
        """전일 종가 신호 -> 당일 시가 매수."""
        slots = self.portfolio.open_slots(self.cfg.max_positions)
        if slots <= 0:
            return
        candidates: list[tuple[str, float]] = []
        for sym, sf in self._signals.items():
            if self.portfolio.has_position(sym):
                continue
            if prev not in sf.df.index:
                continue
            prev_row = sf.df.loc[prev]
            if not bool(prev_row["entry_signal"]):
                continue
            score = float(prev_row["score_composite"])
            candidates.append((sym, score))
        candidates.sort(key=lambda x: x[1], reverse=True)
        for sym, _ in candidates:
            if self.portfolio.open_slots(self.cfg.max_positions) <= 0:
                break
            if self.portfolio.has_position(sym):
                continue
            row = self._bar_optional(sym, d)
            if row is None:
                continue
            open_px = float(row["open"])
            alloc = min(self.cfg.allocation_per_position, self.portfolio.cash * 0.999)
            fill = buy_at_open(open_px, alloc, self.cfg)
            if fill is None:
                continue
            cost = fill.gross + fill.commission + fill.tax
            if cost > self.portfolio.cash:
                continue
            self.portfolio.cash -= cost
            hard_stop = fill.price * (1.0 - self.cfg.stop_loss_pct)
            tid = self.portfolio.next_trade_id()
            self.portfolio.add_trade(
                TradeRecord(
                    trade_id=tid,
                    date=d.date(),
                    symbol=sym,
                    side=Side.BUY,
                    quantity=fill.quantity,
                    price=fill.price,
                    commission=fill.commission,
                    tax=fill.tax,
                    reason="ENTRY_SIGNAL",
                )
            )
            day_high = float(row["high"])
            self.portfolio.positions[sym] = Position(
                symbol=sym,
                quantity=fill.quantity,
                avg_entry_price=fill.price,
                entry_date=d.date(),
                stop_price=hard_stop,
                highest_high_since_entry=max(day_high, open_px),
            )

    def _update_highs(self, d: pd.Timestamp) -> None:
        for sym, pos in self.portfolio.positions.items():
            row = self._bar_optional(sym, d)
            if row is None:
                continue
            h = float(row["high"])
            pos.highest_high_since_entry = max(pos.highest_high_since_entry, h)

    def run(self) -> BacktestResult:
        self.portfolio = Portfolio(cash=self.cfg.initial_capital)
        cal = self._calendar()
        # union calendar: global warmup based on calendar length, but per-symbol signals are already False when data is missing.
        start_i = min(self.cfg.min_bars_for_signals, max(0, len(cal) - 1))
        for i in range(start_i, len(cal)):
            d = cal[i]
            if i == 0:
                continue
            prev = cal[i - 1]

            # --- 매도: 손절·트레일링(시가/저가) 후 MA20 이탈(전일 신호 -> 당일 시가) ---
            self._process_stop_exits(d)
            self._process_ma20_open_exits(d, prev)
            # --- 매수: 전일 종가 신호 -> 당일 시가 ---
            self._process_entries(d, prev)
            # --- 보유 고점 갱신 ---
            self._update_highs(d)
            # --- 평가 ---
            mv = self._market_value(d)
            self.portfolio.record_equity(d.date(), mv)

        tdf = trades_to_dataframe(self.portfolio.trades)
        edf = equity_to_dataframe(self.portfolio.equity_history)
        summ = compute_metrics(edf, tdf)
        return BacktestResult(
            trade_log_df=tdf,
            equity_curve_df=edf,
            summary=summ,
        )
