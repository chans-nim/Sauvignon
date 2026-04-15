"""Signal generation only — no execution. Uses only data available at each bar close."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from config import BacktestConfig


class SignalModel:
    """
    백테스트와 실시간 공통 지표/신호 계산기.
    ``prepare_indicators``는 닫힌 봉 기준으로만 rolling 하며, 마지막 행이 '현재 바' 스냅샷이다.
    """

    def __init__(self, cfg: BacktestConfig) -> None:
        self.cfg = cfg

    def prepare_indicators(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        """OHLCV(인덱스: 시각)를 받아 ``compute_strategy_indicators``와 동일한 컬럼을 반환한다."""
        return compute_strategy_indicators(ohlcv, self.cfg)


@dataclass(frozen=True)
class SignalFrame:
    """Per-symbol signals indexed by date (one row per trading day)."""

    df: pd.DataFrame

    @property
    def entry_signal(self) -> pd.Series:
        return self.df["entry_signal"]

    @property
    def exit_ma20_signal(self) -> pd.Series:
        return self.df["exit_ma20_signal"]


def _score_trend_strength(close: pd.Series, ma_fast: pd.Series, ma_slow: pd.Series) -> pd.Series:
    denom = ma_slow.replace(0, np.nan)
    raw = (close - ma_slow) / denom
    return raw.clip(lower=0).fillna(0)


def _score_volume_lift(volume: pd.Series, vol_ma: pd.Series) -> pd.Series:
    denom = vol_ma.replace(0, np.nan)
    raw = volume / denom - 1.0
    return raw.clip(lower=0).fillna(0)


def _score_near_high(high: pd.Series, close: pd.Series, window: int) -> pd.Series:
    roll_max = high.rolling(window, min_periods=window).max()
    denom = roll_max.replace(0, np.nan)
    return (close / denom).clip(0, 1).fillna(0)


def compute_strategy_indicators(ohlcv: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
    """
    백테스트·실시간 공통 전략 지표/신호 컬럼.
    인덱스는 시각(DatetimeIndex), 열은 open/high/low/close/volume(지표 계산에 close/high/volume 사용).

    - entry_signal: t 종가 시점 진입 조건 충족 여부
    - exit_ma20_signal: t 종가 기준 MA20 이탈 여부
    """
    df = ohlcv.sort_index().copy()
    c = df["close"]
    h = df["high"]
    v = df["volume"]

    ma20 = c.rolling(cfg.ma_fast, min_periods=cfg.ma_fast).mean()
    ma60 = c.rolling(cfg.ma_slow, min_periods=cfg.ma_slow).mean()
    vol_ma20 = v.rolling(cfg.vol_ma, min_periods=cfg.vol_ma).mean()

    entry = (c > ma20) & (ma20 > ma60) & (v > vol_ma20)
    exit_ma = c < ma20

    trend = _score_trend_strength(c, ma20, ma60)
    vol_s = _score_volume_lift(v, vol_ma20)
    hi_s = _score_near_high(h, c, cfg.ma_fast)
    composite = (trend * 0.4 + vol_s * 0.3 + hi_s * 0.3).fillna(0)

    return pd.DataFrame(
        {
            "close": c,
            "ma20": ma20,
            "ma60": ma60,
            "vol_ma20": vol_ma20,
            "entry_signal": entry.fillna(False),
            "exit_ma20_signal": exit_ma.fillna(False),
            "score_trend": trend,
            "score_volume": vol_s,
            "score_near_high": hi_s,
            "score_composite": composite,
        },
        index=df.index,
    )


def build_signals(ohlcv: pd.DataFrame, cfg: BacktestConfig) -> SignalFrame:
    """
    All columns use only past/current bar OHLCV (rolling includes current close only).
    entry_signal True at t: long setup at t's close -> eligible to buy at t+1 open.
    exit_ma20_signal True at t: exit at t+1 open (symmetric with entry).
    """
    return SignalFrame(df=compute_strategy_indicators(ohlcv, cfg))
