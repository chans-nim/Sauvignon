"""10 preset strategies (Strategy Builder 참고) for backtest signal generation."""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import BacktestConfig
from signals import compute_strategy_indicators
from strategy_core.base_strategy import BaseBacktestStrategy
from strategy_core.indicators import (
    close_position_in_range,
    cross_above,
    cross_below,
    roc,
    rolling_high,
    sma,
    std,
    zscore,
)
from strategy_core.registry import register


@register("trend_filter", "trend")
class TrendFilterStrategy(BaseBacktestStrategy):
    name = "추세 필터"
    description = "MA 위에서만 진입, MA 아래면 청산."

    def __init__(self, ma: int = 60) -> None:
        self.ma = int(ma)
        self.required_bars = max(65, self.ma + 5)

    def prepare_indicators(self, ohlcv: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
        c = ohlcv["close"].astype(float)
        ma = sma(c, self.ma)
        return pd.DataFrame({"ma": ma}, index=ohlcv.index)

    def generate_signals(self, ohlcv: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
        ind = self.prepare_indicators(ohlcv, cfg)
        c = ohlcv["close"].astype(float)
        entry = c > ind["ma"]
        exit_ = c < ind["ma"]
        score = ((c / ind["ma"]) - 1.0).clip(lower=0).fillna(0)
        return pd.DataFrame({"entry_signal": entry, "exit_ma20_signal": exit_, "score_composite": score}, index=ohlcv.index)


@register("golden_cross", "trend")
class GoldenCrossStrategy(BaseBacktestStrategy):
    name = "골든크로스"
    description = "단기 MA 상향 돌파 시 진입, 하향 돌파 시 청산."

    def __init__(self, fast: int = 5, slow: int = 20) -> None:
        self.fast = int(fast)
        self.slow = int(slow)
        self.required_bars = max(65, self.slow + 5)

    def prepare_indicators(self, ohlcv: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
        c = ohlcv["close"].astype(float)
        f = sma(c, self.fast)
        s = sma(c, self.slow)
        return pd.DataFrame({"ma_fast": f, "ma_slow": s}, index=ohlcv.index)

    def generate_signals(self, ohlcv: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
        ind = self.prepare_indicators(ohlcv, cfg)
        entry = cross_above(ind["ma_fast"], ind["ma_slow"])
        exit_ = cross_below(ind["ma_fast"], ind["ma_slow"])
        score = ((ind["ma_fast"] / ind["ma_slow"]) - 1.0).clip(lower=0).fillna(0)
        return pd.DataFrame({"entry_signal": entry, "exit_ma20_signal": exit_, "score_composite": score}, index=ohlcv.index)


@register("momentum", "trend")
class MomentumStrategy(BaseBacktestStrategy):
    name = "모멘텀"
    description = "N일 수익률이 양수면 진입, 음수면 청산."

    def __init__(self, lookback: int = 20) -> None:
        self.lookback = int(lookback)
        self.required_bars = max(65, self.lookback + 5)

    def prepare_indicators(self, ohlcv: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
        c = ohlcv["close"].astype(float)
        r = roc(c, self.lookback)
        return pd.DataFrame({"roc": r}, index=ohlcv.index)

    def generate_signals(self, ohlcv: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
        ind = self.prepare_indicators(ohlcv, cfg)
        entry = ind["roc"] > 0
        exit_ = ind["roc"] < 0
        score = ind["roc"].clip(lower=0).fillna(0)
        return pd.DataFrame({"entry_signal": entry, "exit_ma20_signal": exit_, "score_composite": score}, index=ohlcv.index)


@register("week52_high", "breakout")
class Week52HighStrategy(BaseBacktestStrategy):
    name = "52주 신고가"
    description = "252거래일 최고가 돌파 시 진입, MA 이탈 시 청산."

    def __init__(self, window: int = 252, exit_ma: int = 20) -> None:
        self.window = int(window)
        self.exit_ma = int(exit_ma)
        self.required_bars = max(65, self.window + 5, self.exit_ma + 5)

    def prepare_indicators(self, ohlcv: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
        h = ohlcv["high"].astype(float)
        c = ohlcv["close"].astype(float)
        hh = rolling_high(h, self.window).shift(1)
        ma = sma(c, self.exit_ma)
        return pd.DataFrame({"prev_hh": hh, "exit_ma": ma}, index=ohlcv.index)

    def generate_signals(self, ohlcv: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
        ind = self.prepare_indicators(ohlcv, cfg)
        c = ohlcv["close"].astype(float)
        entry = c > ind["prev_hh"]
        exit_ = c < ind["exit_ma"]
        score = ((c / ind["prev_hh"]) - 1.0).clip(lower=0).fillna(0)
        return pd.DataFrame({"entry_signal": entry, "exit_ma20_signal": exit_, "score_composite": score}, index=ohlcv.index)


@register("consecutive", "trend")
class ConsecutiveStrategy(BaseBacktestStrategy):
    name = "연속 상승/하락"
    description = "N일 연속 상승이면 진입, M일 연속 하락이면 청산."

    def __init__(self, buy_days: int = 3, sell_days: int | None = None) -> None:
        self.buy_days = int(buy_days)
        self.sell_days = int(sell_days if sell_days is not None else buy_days)
        self.required_bars = max(65, self.buy_days + 5, self.sell_days + 5)

    def prepare_indicators(self, ohlcv: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
        c = ohlcv["close"].astype(float)
        up = (c > c.shift(1)).astype(int)
        dn = (c < c.shift(1)).astype(int)
        up_run = up.rolling(self.buy_days, min_periods=self.buy_days).sum()
        dn_run = dn.rolling(self.sell_days, min_periods=self.sell_days).sum()
        return pd.DataFrame({"up_run": up_run, "dn_run": dn_run}, index=ohlcv.index)

    def generate_signals(self, ohlcv: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
        ind = self.prepare_indicators(ohlcv, cfg)
        entry = ind["up_run"] >= self.buy_days
        exit_ = ind["dn_run"] >= self.sell_days
        score = (ind["up_run"] / float(self.buy_days)).clip(0, 1).fillna(0)
        return pd.DataFrame({"entry_signal": entry, "exit_ma20_signal": exit_, "score_composite": score}, index=ohlcv.index)


@register("disparity", "mean_reversion")
class DisparityStrategy(BaseBacktestStrategy):
    name = "이격도"
    description = "MA 대비 과매도면 진입, 회귀하면 청산."

    def __init__(self, ma: int = 20, buy_below: float = -0.06, sell_above: float = -0.01) -> None:
        self.ma = int(ma)
        self.buy_below = float(buy_below)
        self.sell_above = float(sell_above)
        self.required_bars = max(65, self.ma + 5)

    def prepare_indicators(self, ohlcv: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
        c = ohlcv["close"].astype(float)
        ma = sma(c, self.ma)
        disp = (c / ma) - 1.0
        return pd.DataFrame({"disp": disp}, index=ohlcv.index)

    def generate_signals(self, ohlcv: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
        ind = self.prepare_indicators(ohlcv, cfg)
        entry = ind["disp"] <= self.buy_below
        exit_ = ind["disp"] >= self.sell_above
        score = (-ind["disp"]).clip(lower=0).fillna(0)
        return pd.DataFrame({"entry_signal": entry, "exit_ma20_signal": exit_, "score_composite": score}, index=ohlcv.index)


@register("breakout_fail", "risk")
class BreakoutFailStrategy(BaseBacktestStrategy):
    name = "돌파 실패"
    description = "최근 고점 돌파 후 실패(종가 약세) 시 청산 신호 강화."

    def __init__(self, lookback: int = 60, entry_ma: int = 20) -> None:
        self.lookback = int(lookback)
        self.entry_ma = int(entry_ma)
        self.required_bars = max(65, self.lookback + 5, self.entry_ma + 5)

    def prepare_indicators(self, ohlcv: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
        h = ohlcv["high"].astype(float)
        c = ohlcv["close"].astype(float)
        prev_hh = rolling_high(h, self.lookback).shift(1)
        ma = sma(c, self.entry_ma)
        return pd.DataFrame({"prev_hh": prev_hh, "ma": ma}, index=ohlcv.index)

    def generate_signals(self, ohlcv: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
        ind = self.prepare_indicators(ohlcv, cfg)
        c = ohlcv["close"].astype(float)
        entry = (c > ind["ma"]) & (c > ind["prev_hh"] * 0.98)
        # 돌파 근처까지 갔다가 종가가 prev_hh 아래면 실패로 보고 exit
        exit_ = (ohlcv["high"].astype(float) >= ind["prev_hh"]) & (c < ind["prev_hh"])
        score = ((c / ind["ma"]) - 1.0).clip(lower=0).fillna(0)
        return pd.DataFrame({"entry_signal": entry, "exit_ma20_signal": exit_, "score_composite": score}, index=ohlcv.index)


@register("strong_close", "momentum")
class StrongCloseStrategy(BaseBacktestStrategy):
    name = "강한 종가"
    description = "고가 대비 종가 위치가 높으면 진입."

    def __init__(self, threshold: float = 0.8, exit_ma: int = 20) -> None:
        self.threshold = float(threshold)
        self.exit_ma = int(exit_ma)
        self.required_bars = max(65, self.exit_ma + 5)

    def prepare_indicators(self, ohlcv: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
        pos = close_position_in_range(
            ohlcv["close"].astype(float),
            ohlcv["high"].astype(float),
            ohlcv["low"].astype(float),
        )
        ma = sma(ohlcv["close"].astype(float), self.exit_ma)
        return pd.DataFrame({"pos": pos, "exit_ma": ma}, index=ohlcv.index)

    def generate_signals(self, ohlcv: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
        ind = self.prepare_indicators(ohlcv, cfg)
        entry = ind["pos"] >= self.threshold
        exit_ = ohlcv["close"].astype(float) < ind["exit_ma"]
        score = (ind["pos"] - self.threshold).clip(lower=0).fillna(0)
        return pd.DataFrame({"entry_signal": entry, "exit_ma20_signal": exit_, "score_composite": score}, index=ohlcv.index)


@register("volatility", "breakout")
class VolatilityExpansionStrategy(BaseBacktestStrategy):
    name = "변동성 확장"
    description = "변동성이 낮은 구간 이후 확장 시 진입."

    def __init__(self, vol_window: int = 20, low_window: int = 60, exit_ma: int = 20) -> None:
        self.vol_window = int(vol_window)
        self.low_window = int(low_window)
        self.exit_ma = int(exit_ma)
        self.required_bars = max(65, self.low_window + 5, self.exit_ma + 5)

    def prepare_indicators(self, ohlcv: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
        c = ohlcv["close"].astype(float)
        ret = c.pct_change()
        vol = ret.rolling(self.vol_window, min_periods=self.vol_window).std(ddof=0)
        vol_low = vol.rolling(self.low_window, min_periods=self.low_window).min()
        ma = sma(c, self.exit_ma)
        return pd.DataFrame({"vol": vol, "vol_low": vol_low, "exit_ma": ma}, index=ohlcv.index)

    def generate_signals(self, ohlcv: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
        ind = self.prepare_indicators(ohlcv, cfg)
        # low 근처에서 vol이 증가하면 entry
        entry = (
            (ind["vol_low"].notna())
            & (ind["vol"] <= ind["vol_low"] * 1.1)
            & (ind["vol"].shift(1) <= ind["vol"].shift(2))
            & (ind["vol"] > ind["vol"].shift(1))
        )
        exit_ = ohlcv["close"].astype(float) < ind["exit_ma"]
        score = (ind["vol"] / ind["vol_low"]).replace([np.inf, -np.inf], np.nan).fillna(0).clip(lower=0)
        return pd.DataFrame({"entry_signal": entry, "exit_ma20_signal": exit_, "score_composite": score}, index=ohlcv.index)


@register("mean_reversion", "mean_reversion")
class MeanReversionStrategy(BaseBacktestStrategy):
    name = "평균회귀"
    description = "Z-score 과매도에서 진입, 0 회귀 시 청산."

    def __init__(self, window: int = 20, z_buy: float = -1.5, z_sell: float = -0.2) -> None:
        self.window = int(window)
        self.z_buy = float(z_buy)
        self.z_sell = float(z_sell)
        self.required_bars = max(65, self.window + 5)

    def prepare_indicators(self, ohlcv: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
        c = ohlcv["close"].astype(float)
        z = zscore(c, self.window)
        return pd.DataFrame({"z": z}, index=ohlcv.index)

    def generate_signals(self, ohlcv: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
        ind = self.prepare_indicators(ohlcv, cfg)
        entry = ind["z"] <= self.z_buy
        exit_ = ind["z"] >= self.z_sell
        score = (-ind["z"]).clip(lower=0).fillna(0)
        return pd.DataFrame({"entry_signal": entry, "exit_ma20_signal": exit_, "score_composite": score}, index=ohlcv.index)


@register("ma_volume", "trend")
class MAVolumeStrategy(BaseBacktestStrategy):
    """기존 backtest_mvp/signals.py의 기본 전략 (MA+거래량) 래퍼."""

    name = "MA+Volume 기본전략"
    description = "close>ma20, ma20>ma60, volume>vol_ma20"

    def __init__(self) -> None:
        self.required_bars = 65

    def prepare_indicators(self, ohlcv: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
        return compute_strategy_indicators(ohlcv, cfg)

    def generate_signals(self, ohlcv: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
        return compute_strategy_indicators(ohlcv, cfg)
