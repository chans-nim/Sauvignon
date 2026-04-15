"""주도주 탈락 필터."""

from __future__ import annotations

from .models import StockCandidate, StockSnapshot
from .scanner_config import ScannerConfig


def compute_volume_ratio(snapshot: StockSnapshot, candidate: StockCandidate) -> float:
    """
    거래량 비율: 당일 누적(또는 스냅샷) / 분봉 평균 거래량 정규화 근사.
    """
    rf = candidate.raw_factors or {}
    if "volume_ratio" in rf:
        try:
            return float(rf["volume_ratio"])
        except (TypeError, ValueError):
            pass
    vol = snapshot.volume
    avg = snapshot.extra.get("avg_bar_volume") if snapshot.extra else None
    try:
        avg_f = float(avg) if avg is not None else 0.0
    except (TypeError, ValueError):
        avg_f = 0.0
    n = int(snapshot.extra.get("bar_count", 0) or 0) if snapshot.extra else 0
    if vol is None or avg_f <= 0 or n <= 0:
        return 1.0
    est = avg_f * n
    if est <= 0:
        return 1.0
    ratio = float(vol) / est
    return max(0.0, min(5.0, ratio))


def passes_filters(
    candidate: StockCandidate,
    snapshot: StockSnapshot,
    config: ScannerConfig,
    sector_score: float,
) -> tuple[bool, str]:
    """(통과 여부, 거절 사유)."""
    if snapshot.price < float(config.min_price):
        return False, f"price_below_min({snapshot.price}<{config.min_price})"

    vt = snapshot.value_traded or 0.0
    if vt < float(config.min_value_traded):
        return False, f"value_traded_low({vt}<{config.min_value_traded})"

    vr = compute_volume_ratio(snapshot, candidate)
    if vr < float(config.min_volume_ratio):
        return False, f"volume_ratio_low({vr:.3f}<{config.min_volume_ratio})"

    if config.vwap_required and snapshot.vwap is not None and snapshot.price < snapshot.vwap:
        return False, f"below_vwap(price={snapshot.price},vwap={snapshot.vwap})"

    high = snapshot.high
    if high is not None and high > 0 and snapshot.price > 0:
        pullback = (high - snapshot.price) / high
        if pullback > float(config.max_intraday_pullback_pct):
            return False, f"pullback_too_deep({pullback:.4f}>{config.max_intraday_pullback_pct})"

    if sector_score < float(config.min_evaluated_sector_score):
        return False, f"eval_sector_score_low({sector_score}<{config.min_evaluated_sector_score})"

    return True, ""
