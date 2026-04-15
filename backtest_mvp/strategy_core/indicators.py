"""Lightweight indicators used by preset strategies (pandas only)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window, min_periods=window).mean()


def roc(s: pd.Series, window: int) -> pd.Series:
    prev = s.shift(window)
    return (s / prev - 1.0).replace([np.inf, -np.inf], np.nan)


def cross_above(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a > b) & (a.shift(1) <= b.shift(1))


def cross_below(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a < b) & (a.shift(1) >= b.shift(1))


def rolling_high(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window, min_periods=window).max()


def rolling_low(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window, min_periods=window).min()


def std(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window, min_periods=window).std(ddof=0)


def zscore(s: pd.Series, window: int) -> pd.Series:
    m = s.rolling(window, min_periods=window).mean()
    sd = std(s, window).replace(0, np.nan)
    return (s - m) / sd


def close_position_in_range(close: pd.Series, high: pd.Series, low: pd.Series) -> pd.Series:
    denom = (high - low).replace(0, np.nan)
    return ((close - low) / denom).clip(0, 1).fillna(0)

