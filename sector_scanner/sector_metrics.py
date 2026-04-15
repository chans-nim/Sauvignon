"""Sector (or stock group) metrics: coherence, breadth, persistence, concentration."""

from __future__ import annotations

from typing import Sequence

import numpy as np
import pandas as pd


def _subset_returns(returns: pd.DataFrame, members: Sequence[str]) -> pd.DataFrame:
    cols = [c for c in members if c in returns.columns]
    if not cols:
        return pd.DataFrame()
    return returns[cols].dropna(how="all", axis=0)


def calc_coherence(returns: pd.DataFrame, members: Sequence[str]) -> float:
    """
    Mean pairwise correlation, treating negative correlation as 0 (only positive co-movement counts).
    """
    sub = _subset_returns(returns, members)
    if sub.shape[1] < 2 or len(sub) < 5:
        return 0.0
    corr = sub.corr(numeric_only=True)
    if corr.empty:
        return 0.0
    v = corr.values
    tri = v[np.triu_indices_from(v, k=1)]
    tri = tri[~np.isnan(tri)]
    if tri.size == 0:
        return 0.0
    tri = np.maximum(tri, 0.0)
    return float(np.clip(np.mean(tri), 0.0, 1.0))


def calc_breadth(returns: pd.DataFrame, members: Sequence[str], *, last_n: int = 1) -> float:
    """
    Share of members up on the last day among the last ``last_n`` rows (0~1).
    """
    sub = _subset_returns(returns, members)
    if sub.empty:
        return 0.0
    tail = sub.iloc[-last_n:]
    last = tail.iloc[-1]
    up = (last > 0).sum()
    tot = last.notna().sum()
    if tot <= 0:
        return 0.0
    return float(np.clip(up / float(tot), 0.0, 1.0))


def calc_persistence(returns: pd.DataFrame, members: Sequence[str]) -> float:
    """
    Uptrend persistence: fraction of days where the group's mean daily return is positive (0~1).
    """
    sub = _subset_returns(returns, members)
    if sub.empty or len(sub) < 3:
        return 0.0
    daily = sub.mean(axis=1, skipna=True)
    pos = (daily > 0).sum()
    tot = daily.notna().sum()
    if tot <= 0:
        return 0.0
    return float(np.clip(pos / float(tot), 0.0, 1.0))


def calc_concentration(returns: pd.DataFrame, members: Sequence[str]) -> float:
    """
    Name concentration: Herfindahl of |return| weights on the last day (0~1, higher = more concentrated).
    """
    sub = _subset_returns(returns, members)
    if sub.empty:
        return 0.0
    last = sub.iloc[-1].astype(float)
    last = last[np.isfinite(last)]
    if last.empty:
        return 0.0
    w = np.abs(last.values)
    s = float(w.sum())
    if s <= 1e-12:
        return 0.0
    p = w / s
    h = float(np.sum(p**2))
    return float(np.clip(h, 0.0, 1.0))


def calc_relative_return(
    returns: pd.DataFrame,
    members: Sequence[str],
    market_returns: pd.Series,
) -> float:
    """Recent median of (sector mean return - market mean return), clipped to 0~1."""
    sub = _subset_returns(returns, members)
    if sub.empty:
        return 0.0
    tail = sub.iloc[-min(5, len(sub)) :]
    mkt = market_returns.reindex(tail.index)
    sector_daily = tail.mean(axis=1, skipna=True)
    rel = sector_daily.astype(float) - mkt.astype(float)
    rel = rel.replace([np.inf, -np.inf], np.nan).dropna()
    if rel.empty:
        return 0.0
    med = float(np.nanmedian(rel.values))
    return float(np.clip(0.5 + med * 30.0, 0.0, 1.0))
