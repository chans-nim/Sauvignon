"""Symbol-local OHLCV history built from streaming market events."""

from __future__ import annotations

import logging

import pandas as pd

from models import MarketEvent

logger = logging.getLogger(__name__)


class MarketDataHandler:
    """
    종목별 최신 시세 및 최근 봉 히스토리.

    - ``merge_ticks_to_minute_bars=True``(기본): 동일 분 단위 이벤트를 한 봉으로 병합 (1분봉 재구성).
    - ``False``: 틱마다 고유 타임스탬프로 봉을 쌓아 분 집계 없이 신호에 사용 가능.
    """

    def __init__(
        self,
        max_rows_per_symbol: int = 500,
        merge_ticks_to_minute_bars: bool = True,
    ) -> None:
        self._max_rows = max_rows_per_symbol
        self._merge = merge_ticks_to_minute_bars
        self._mono_ns = 0
        self._frames: dict[str, pd.DataFrame] = {}

    def update_event(self, event: MarketEvent) -> None:
        """이벤트로 내부 OHLCV를 갱신한다."""
        sym = event.symbol
        ts = pd.Timestamp(event.event_time)
        if self._merge:
            bar_ts = ts.floor("min")
        else:
            self._mono_ns += 1
            bar_ts = ts + pd.Timedelta(nanoseconds=self._mono_ns)

        o = float(event.open if event.open is not None else event.price)
        h = float(event.high if event.high is not None else event.price)
        lo = float(event.low if event.low is not None else event.price)
        c = float(event.close if event.close is not None else event.price)
        v = float(event.volume if event.volume is not None else 0.0)

        df = self._frames.get(sym)
        row = {
            "date": bar_ts,
            "open": o,
            "high": h,
            "low": lo,
            "close": c,
            "volume": v,
        }

        if df is None or df.empty:
            self._frames[sym] = pd.DataFrame([row]).set_index("date")
            return

        if bar_ts in df.index:
            existing = df.loc[bar_ts]
            new_open = float(existing["open"])
            new_high = max(float(existing["high"]), h)
            new_low = min(float(existing["low"]), lo)
            new_vol = float(existing["volume"]) + v
            df.loc[bar_ts, ["open", "high", "low", "close", "volume"]] = [
                new_open,
                new_high,
                new_low,
                c,
                new_vol,
            ]
        else:
            df2 = pd.DataFrame([row]).set_index("date")
            df = pd.concat([df, df2]).sort_index()
            if len(df) > self._max_rows:
                df = df.iloc[-self._max_rows :]
            self._frames[sym] = df

    def get_latest_price(self, symbol: str) -> float | None:
        df = self._frames.get(symbol)
        if df is None or df.empty:
            return None
        return float(df["close"].iloc[-1])

    def get_latest_snapshot(self, symbol: str) -> dict[str, float | str | None]:
        df = self._frames.get(symbol)
        if df is None or df.empty:
            return {"symbol": symbol, "close": None}
        last = df.iloc[-1]
        return {
            "symbol": symbol,
            "date": df.index[-1].isoformat(),
            "open": float(last["open"]),
            "high": float(last["high"]),
            "low": float(last["low"]),
            "close": float(last["close"]),
            "volume": float(last["volume"]),
        }

    def get_symbol_frame(self, symbol: str) -> pd.DataFrame:
        """열: date, open, high, low, close, volume. 날짜 오름차순."""
        df = self._frames.get(symbol)
        if df is None or df.empty:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        out = df.reset_index().rename(columns={"index": "date"})
        if "date" not in out.columns:
            out = out.rename(columns={out.columns[0]: "date"})
        return out.sort_values("date").reset_index(drop=True)

    def has_enough_data(self, symbol: str, min_length: int) -> bool:
        df = self.get_symbol_frame(symbol)
        return len(df) >= min_length
