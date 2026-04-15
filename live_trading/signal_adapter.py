"""Adapts backtest ``SignalModel`` indicators to live ``SignalDecision`` objects."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

from live_config import LiveTradingConfig
from models import LivePosition, PositionStatus, SignalDecision

if TYPE_CHECKING:
    pass

_BT = Path(__file__).resolve().parent.parent / "backtest_mvp"
if _BT.is_dir() and str(_BT) not in sys.path:
    sys.path.insert(0, str(_BT))

from signals import SignalModel  # noqa: E402

logger = logging.getLogger(__name__)


class SignalAdapter:
    """
    ``SignalModel.prepare_indicators`` → 백테스트의 ``compute_strategy_indicators``와 동일한 수식.
    입력(DataFrame 형태·타임존)만 어댑터에서 정규화하고, 전략 수학은 ``backtest_mvp.signals`` 단일 경로를 쓴다.
    포지션 손절·트레일은 ``LiveTradingConfig``와 ``LivePosition`` 상태로 평가한다.
    """

    def __init__(self, signal_model: SignalModel, config: LiveTradingConfig) -> None:
        self._model = signal_model
        self._config = config
        self._min_bars = max(signal_model.cfg.ma_slow + 5, signal_model.cfg.min_bars_for_signals)

    def _frame_to_ohlcv_indexed(self, frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return frame
        df = frame.copy()
        df["date"] = pd.to_datetime(df["date"], utc=True)
        return df.set_index("date")[["open", "high", "low", "close", "volume"]].sort_index()

    def build_metadata(self, frame: pd.DataFrame) -> dict[str, Any]:
        """진단용 메타데이터 (마지막 행 기준)."""
        ohlc = self._frame_to_ohlcv_indexed(frame)
        if len(ohlc) < self._min_bars:
            return {"ready": False, "bars": len(ohlc), "min_bars": self._min_bars}
        ind = self._model.prepare_indicators(ohlc)
        last = ind.iloc[-1]
        return {
            "ready": True,
            "bars": len(ohlc),
            "close": float(last["close"]),
            "ma20": float(last["ma20"]) if pd.notna(last["ma20"]) else None,
            "ma60": float(last["ma60"]) if pd.notna(last["ma60"]) else None,
            "entry_signal": bool(last["entry_signal"]),
            "exit_ma20_signal": bool(last["exit_ma20_signal"]),
            "score_composite": float(last["score_composite"]),
        }

    def evaluate_entry(
        self,
        symbol: str,
        frame: pd.DataFrame,
        has_position: bool,
    ) -> SignalDecision:
        if has_position:
            return SignalDecision(
                symbol=symbol,
                action="NONE",
                score=0.0,
                reason="already_has_position",
                stop_price=None,
                metadata=self.build_metadata(frame),
            )
        ohlc = self._frame_to_ohlcv_indexed(frame)
        if len(ohlc) < self._min_bars:
            return SignalDecision(
                symbol=symbol,
                action="NONE",
                score=0.0,
                reason="insufficient_data",
                stop_price=None,
                metadata=self.build_metadata(frame),
            )
        ind = self._model.prepare_indicators(ohlc)
        last = ind.iloc[-1]
        meta = self.build_metadata(frame)
        if bool(last["entry_signal"]):
            last_close = float(last["close"])
            stop = last_close * (1.0 - self._config.stop_loss_pct)
            return SignalDecision(
                symbol=symbol,
                action="BUY",
                score=float(last["score_composite"]),
                reason="entry_ma_volume",
                stop_price=stop,
                metadata=meta,
            )
        return SignalDecision(
            symbol=symbol,
            action="NONE",
            score=float(last["score_composite"]),
            reason="no_entry_signal",
            stop_price=None,
            metadata=meta,
        )

    def evaluate_exit(self, position: LivePosition, frame: pd.DataFrame) -> SignalDecision:
        symbol = position.symbol
        if position.status not in (PositionStatus.LONG, PositionStatus.EXITING) or position.qty <= 0:
            return SignalDecision(
                symbol=symbol,
                action="NONE",
                score=0.0,
                reason="not_long",
                stop_price=position.stop_price,
                metadata=self.build_metadata(frame),
            )
        ohlc = self._frame_to_ohlcv_indexed(frame)
        if ohlc.empty:
            return SignalDecision(
                symbol=symbol,
                action="NONE",
                score=0.0,
                reason="no_bars",
                stop_price=position.stop_price,
                metadata={},
            )
        last = ohlc.iloc[-1]
        o = float(last["open"])
        lo = float(last["low"])
        hi = float(last["high"])

        hard = position.stop_price
        hh = max(position.highest_price, hi, position.avg_price)
        trail = hh * (1.0 - self._config.trailing_stop_pct)
        levels = [trail]
        if hard is not None:
            levels.append(hard)
        eff = max(levels)

        meta = self.build_metadata(frame)

        if o <= eff or lo <= eff:
            return SignalDecision(
                symbol=symbol,
                action="SELL",
                score=1.0,
                reason="stop_or_trailing",
                stop_price=eff,
                metadata=meta,
            )

        if len(ohlc) >= self._min_bars:
            ind = self._model.prepare_indicators(ohlc)
            row = ind.iloc[-1]
            if bool(row["exit_ma20_signal"]):
                return SignalDecision(
                    symbol=symbol,
                    action="SELL",
                    score=float(row["score_composite"]),
                    reason="ma20_break",
                    stop_price=position.stop_price,
                    metadata=meta,
                )

        return SignalDecision(
            symbol=symbol,
            action="NONE",
            score=0.0,
            reason="hold",
            stop_price=position.stop_price,
            metadata=meta,
        )
