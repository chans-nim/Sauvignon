"""스캐너 설정: dataclass + 검증."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass


def _validate_hhmm(s: str, field_name: str) -> None:
    if not re.fullmatch(r"\d{2}:\d{2}", str(s).strip()):
        raise ValueError(f"{field_name} must be HH:MM, got {s!r}")
    hh, mm = str(s).strip().split(":")
    h, m = int(hh), int(mm)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"{field_name} has invalid clock values: {s!r}")


@dataclass
class ScannerConfig:
    """장중 주도 섹터 / 주도주 스캐너 설정."""

    mode: str = "mock"
    top_sector_n: int = 5
    top_stock_k_per_sector: int = 5
    candidate_pool_limit: int = 200
    sector_refresh_seconds: int = 300
    ranking_refresh_seconds: int = 120
    intraday_refresh_seconds: int = 60
    websocket_enabled: bool = False
    min_price: float = 1_000.0
    min_value_traded: float = 500_000_000.0
    min_volume_ratio: float = 0.8
    max_intraday_pullback_pct: float = 0.05
    vwap_required: bool = False
    use_program_flow: bool = True
    use_foreign_institution_flow: bool = True
    output_dir: str = "sector_scanner/output"
    state_file: str = "sector_scanner/output/scanner_state.json"
    log_file: str = "sector_scanner/output/scanner.log"
    timezone: str = "Asia/Seoul"
    market_open_time: str = "09:00"
    market_close_time: str = "15:30"
    score_round_digits: int = 4
    # --- §20-1 실시간 틱 보정 (최근 N분) ---
    realtime_tick_window_minutes: float = 5.0
    realtime_weight_intensity: float = 8.0
    realtime_bonus_new_high: float = 4.0
    realtime_penalty_below_vwap: float = 5.0
    # --- 대형 유니버스 + 동적 섹터 + 검증 ---
    use_universe_pipeline: bool = True
    universe_target_size: int = 400
    universe_min_value_traded: float = 200_000_000.0
    dynamic_n_clusters: int = 28
    dynamic_min_cluster_size: int = 5
    dynamic_max_cluster_size: int = 180
    sector_metric_window_days: int = 60
    sector_w_coherence: float = 0.25
    sector_w_breadth: float = 0.20
    sector_w_persistence: float = 0.20
    sector_w_relative_return: float = 0.25
    sector_w_concentration: float = 0.10
    sector_min_members: int = 4
    sector_min_coherence: float = 0.05
    sector_min_breadth: float = 0.08
    sector_max_concentration: float = 0.92
    stock_alpha_sector: float = 0.22
    stock_beta_breadth: float = 0.12
    min_evaluated_sector_score: float = 10.0
    stock_concentration_penalty_scale: float = 18.0

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        m = str(self.mode).strip().lower()
        if m not in ("mock", "paper", "real"):
            raise ValueError(f"mode must be one of mock|paper|real, got {self.mode!r}")
        object.__setattr__(self, "mode", m)

        for name, v in (
            ("top_sector_n", self.top_sector_n),
            ("top_stock_k_per_sector", self.top_stock_k_per_sector),
            ("candidate_pool_limit", self.candidate_pool_limit),
            ("sector_refresh_seconds", self.sector_refresh_seconds),
            ("ranking_refresh_seconds", self.ranking_refresh_seconds),
            ("intraday_refresh_seconds", self.intraday_refresh_seconds),
            ("score_round_digits", self.score_round_digits),
        ):
            if not isinstance(v, int) or int(v) < 1:
                raise ValueError(f"{name} must be int >= 1, got {v!r}")

        for name, v in (
            ("min_price", self.min_price),
            ("min_value_traded", self.min_value_traded),
            ("min_volume_ratio", self.min_volume_ratio),
            ("max_intraday_pullback_pct", self.max_intraday_pullback_pct),
        ):
            if float(v) < 0:
                raise ValueError(f"{name} must be non-negative, got {v!r}")

        _validate_hhmm(self.market_open_time, "market_open_time")
        _validate_hhmm(self.market_close_time, "market_close_time")

        if not str(self.timezone).strip():
            raise ValueError("timezone must be non-empty")

        if float(self.realtime_tick_window_minutes) <= 0:
            raise ValueError("realtime_tick_window_minutes must be > 0")
        for name, v in (
            ("realtime_weight_intensity", self.realtime_weight_intensity),
            ("realtime_bonus_new_high", self.realtime_bonus_new_high),
            ("realtime_penalty_below_vwap", self.realtime_penalty_below_vwap),
        ):
            if float(v) < 0:
                raise ValueError(f"{name} must be non-negative, got {v!r}")

        if int(self.universe_target_size) < 10:
            raise ValueError("universe_target_size must be >= 10")
        if int(self.dynamic_n_clusters) < 2:
            raise ValueError("dynamic_n_clusters must be >= 2")
        if int(self.sector_metric_window_days) < 10:
            raise ValueError("sector_metric_window_days must be >= 10")
        for name, v in (
            ("sector_w_coherence", self.sector_w_coherence),
            ("sector_w_breadth", self.sector_w_breadth),
            ("sector_w_persistence", self.sector_w_persistence),
            ("sector_w_relative_return", self.sector_w_relative_return),
            ("sector_w_concentration", self.sector_w_concentration),
            ("stock_alpha_sector", self.stock_alpha_sector),
            ("stock_beta_breadth", self.stock_beta_breadth),
            ("stock_concentration_penalty_scale", self.stock_concentration_penalty_scale),
        ):
            if float(v) < 0:
                raise ValueError(f"{name} must be non-negative, got {v!r}")

    def as_dict(self) -> dict[str, object]:
        return dict(asdict(self))
