"""Live / paper trading configuration."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, fields


def _parse_hhmm(s: str) -> tuple[int, int]:
    if not re.fullmatch(r"\d{2}:\d{2}", s):
        raise ValueError(f"Invalid time format (expected HH:MM): {s!r}")
    h, m = int(s[:2]), int(s[3:5])
    if h > 23 or m > 59:
        raise ValueError(f"Invalid clock value: {s!r}")
    return h, m


@dataclass
class LiveTradingConfig:
    """모의·페이퍼·실거래 공통 라이브 설정."""

    mode: str
    symbols: list[str]
    max_positions: int
    allocation_per_trade: float
    max_daily_new_entries: int
    reentry_cooldown_minutes: int
    no_new_entry_after: str
    force_exit_before_close: bool
    force_exit_time: str
    daily_loss_limit: float
    stop_loss_pct: float
    trailing_stop_pct: float
    poll_interval_seconds: float
    state_path: str
    log_path: str
    timezone: str
    market_open_time: str
    market_close_time: str
    use_mock_stream: bool
    price_rounding_digits: int
    # --- 확장(§7): 분봉 옵션 / 섹터·추격·장초반 회피 / 연속 손실 ---
    merge_ticks_to_minute_bars: bool = True
    symbol_sector: dict[str, str] = field(default_factory=dict)
    max_positions_per_sector: dict[str, int] = field(default_factory=dict)
    max_consecutive_losing_entries: int = 0
    chase_bar_return_limit_pct: float = 0.0
    no_entry_first_minutes_after_open: int = 0

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        allowed = {"mock", "paper", "real"}
        if self.mode not in allowed:
            raise ValueError(f"mode must be one of {allowed}, got {self.mode!r}")
        if not self.symbols:
            raise ValueError("symbols must be non-empty")
        if self.max_positions < 1:
            raise ValueError("max_positions must be >= 1")
        for f in fields(self):
            v = getattr(self, f.name)
            if f.name in (
                "mode",
                "symbols",
                "force_exit_before_close",
                "use_mock_stream",
                "merge_ticks_to_minute_bars",
                "symbol_sector",
                "max_positions_per_sector",
            ):
                continue
            if f.name in (
                "no_new_entry_after",
                "force_exit_time",
                "market_open_time",
                "market_close_time",
            ):
                _parse_hhmm(str(v))
                continue
            if isinstance(v, (int, float)) and f.name != "price_rounding_digits":
                if v < 0:
                    raise ValueError(f"{f.name} must be non-negative, got {v}")
        if self.price_rounding_digits < 0:
            raise ValueError("price_rounding_digits must be non-negative")
        if self.allocation_per_trade <= 0:
            raise ValueError("allocation_per_trade must be positive")
        if self.max_daily_new_entries < 0:
            raise ValueError("max_daily_new_entries must be non-negative")
        _parse_hhmm(self.no_new_entry_after)
        _parse_hhmm(self.force_exit_time)
        _parse_hhmm(self.market_open_time)
        _parse_hhmm(self.market_close_time)
        if self.stop_loss_pct <= 0 or self.stop_loss_pct >= 1:
            raise ValueError("stop_loss_pct must be in (0, 1)")
        if self.trailing_stop_pct <= 0 or self.trailing_stop_pct >= 1:
            raise ValueError("trailing_stop_pct must be in (0, 1)")
        if self.max_consecutive_losing_entries < 0:
            raise ValueError("max_consecutive_losing_entries must be non-negative")
        if self.chase_bar_return_limit_pct < 0:
            raise ValueError("chase_bar_return_limit_pct must be non-negative")
        if self.no_entry_first_minutes_after_open < 0:
            raise ValueError("no_entry_first_minutes_after_open must be non-negative")
        for sec, cap in self.max_positions_per_sector.items():
            if cap < 1:
                raise ValueError(f"max_positions_per_sector[{sec!r}] must be >= 1")

    def as_dict(self) -> dict[str, object]:
        """Serialize-friendly dict (lists and primitives only)."""
        return asdict(self)
