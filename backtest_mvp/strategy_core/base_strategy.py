"""Backtest 전략 베이스 클래스 (신호 생성 전용)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import pandas as pd

from config import BacktestConfig


@dataclass(frozen=True)
class SignalColumns:
    """BacktestEngine이 요구하는 최소 컬럼."""

    entry_signal: str = "entry_signal"
    exit_signal: str = "exit_ma20_signal"  # 엔진 호환을 위해 이름 유지
    score: str = "score_composite"


class BaseBacktestStrategy(ABC):
    """
    전략은 **신호(DataFrame)** 만 만든다.
    - D일 종가 기준 신호 → D+1 시가 체결은 `BacktestEngine`이 책임진다.
    - 미래 데이터 참조 금지: rolling/shift 사용 시 현재 bar까지만 사용.
    """

    name: str = "Base"
    description: str = ""
    required_bars: int = 60
    columns: SignalColumns = SignalColumns()

    @abstractmethod
    def prepare_indicators(self, ohlcv: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
        """(인덱스=일자) OHLCV를 받아 지표 컬럼을 반환한다."""

    @abstractmethod
    def generate_signals(self, ohlcv: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
        """
        최소 컬럼을 포함한 DataFrame 반환:
        - entry_signal (bool)
        - exit_ma20_signal (bool)  # 이름은 유지하되 의미는 'exit 조건'으로 사용
        - score_composite (float)
        """

    def build(self, ohlcv: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
        df = self.generate_signals(ohlcv, cfg).copy()
        cols = self.columns
        for c in (cols.entry_signal, cols.exit_signal, cols.score):
            if c not in df.columns:
                raise ValueError(f"{self.__class__.__name__} missing column: {c}")
        df[cols.entry_signal] = df[cols.entry_signal].fillna(False).astype(bool)
        df[cols.exit_signal] = df[cols.exit_signal].fillna(False).astype(bool)
        df[cols.score] = pd.to_numeric(df[cols.score], errors="coerce").fillna(0.0).astype(float)
        return df

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "required_bars": self.required_bars,
        }

