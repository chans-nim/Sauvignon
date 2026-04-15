"""섹터 메트릭 기반 검증 (통과/탈락 사유)."""

from __future__ import annotations

from typing import Any

from .scanner_config import ScannerConfig


def validate_sector_metrics(
    metrics: dict[str, Any],
    config: ScannerConfig,
    *,
    member_count: int,
) -> tuple[bool, str]:
    """
    최소 인원·일관성·쏠림 한도 등으로 섹터 후보를 거른다.

    Parameters
    ----------
    metrics
        coherence, breadth, persistence, concentration 키를 포함한다고 가정.
    """
    if member_count < int(config.sector_min_members):
        return False, f"too_few_members({member_count}<{config.sector_min_members})"

    coh = float(metrics.get("coherence", 0.0))
    if coh < float(config.sector_min_coherence):
        return False, f"low_coherence({coh:.3f}<{config.sector_min_coherence})"

    conc = float(metrics.get("concentration", 0.0))
    if conc > float(config.sector_max_concentration):
        return False, f"high_concentration({conc:.3f}>{config.sector_max_concentration})"

    br = float(metrics.get("breadth", 0.0))
    if br < float(config.sector_min_breadth):
        return False, f"low_breadth({br:.3f}<{config.sector_min_breadth})"

    return True, ""


class SectorValidator:
    """설정 기반 섹터 검증기."""

    def __init__(self, config: ScannerConfig) -> None:
        self._cfg = config

    def ok(self, metrics: dict[str, Any], *, member_count: int) -> tuple[bool, str]:
        return validate_sector_metrics(metrics, self._cfg, member_count=member_count)
