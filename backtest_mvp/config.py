"""Backtest configuration with validation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BacktestConfig:
    """Single-account portfolio backtest parameters (KRW-style defaults)."""

    initial_capital: float = 100_000_000.0
    max_positions: int = 5
    allocation_per_position: float = 10_000_000.0
    commission_rate: float = 0.00015
    tax_rate: float = 0.0023
    slippage_pct: float = 0.0005
    stop_loss_pct: float = 0.08
    trailing_stop_pct: float = 0.10
    ma_fast: int = 20
    ma_slow: int = 60
    vol_ma: int = 20
    min_bars_for_signals: int = 65

    def validate(self) -> None:
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        if self.max_positions < 1:
            raise ValueError("max_positions must be >= 1")
        if self.allocation_per_position <= 0:
            raise ValueError("allocation_per_position must be positive")
        if self.max_positions * self.allocation_per_position > self.initial_capital * 1.0001:
            raise ValueError(
                "max_positions * allocation_per_position cannot exceed initial_capital"
            )
        if not 0 <= self.commission_rate < 0.05:
            raise ValueError("commission_rate out of range")
        if not 0 <= self.tax_rate < 0.5:
            raise ValueError("tax_rate out of range")
        if not 0 <= self.slippage_pct < 0.05:
            raise ValueError("slippage_pct out of range")
        if not 0 < self.stop_loss_pct < 1:
            raise ValueError("stop_loss_pct must be in (0, 1)")
        if not 0 < self.trailing_stop_pct < 1:
            raise ValueError("trailing_stop_pct must be in (0, 1)")
        if self.ma_fast < 2 or self.ma_slow < 2:
            raise ValueError("MA windows must be >= 2")
        if self.ma_fast >= self.ma_slow:
            raise ValueError("ma_fast must be < ma_slow")
        if self.vol_ma < 2:
            raise ValueError("vol_ma must be >= 2")
        if self.min_bars_for_signals < self.ma_slow + 5:
            raise ValueError("min_bars_for_signals should cover MA warmup")
