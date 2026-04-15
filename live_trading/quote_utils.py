"""Small OHLCV helpers shared by live orchestration (no I/O)."""

from __future__ import annotations

import pandas as pd


def last_bar_return_pct(frame: pd.DataFrame) -> float | None:
    """직전 대비 마지막 봉 수익률 (close 기준). 데이터 부족 시 None."""
    if frame is None or len(frame) < 2:
        return None
    if "close" not in frame.columns:
        return None
    s = frame["close"].astype(float)
    prev = float(s.iloc[-2])
    last = float(s.iloc[-1])
    if prev <= 0:
        return None
    return (last - prev) / prev
