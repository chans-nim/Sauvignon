"""스캐너 도메인 모델 (dataclass)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
from typing import Any

import pandas as pd


def _serialize_value(v: Any) -> Any:
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    if is_dataclass(v):
        return model_to_dict(v)
    if isinstance(v, dict):
        return {str(k): _serialize_value(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_serialize_value(x) for x in v]
    return v


def model_to_dict(obj: Any) -> dict[str, Any]:
    """dataclass / 중첩 구조를 JSON 친화적 dict로 변환."""
    if not is_dataclass(obj):
        raise TypeError(f"model_to_dict expects dataclass instance, got {type(obj)}")
    out: dict[str, Any] = {}
    for f in fields(obj):
        out[f.name] = _serialize_value(getattr(obj, f.name))
    return out


@dataclass
class SectorSnapshot:
    sector_code: str
    sector_name: str
    as_of: pd.Timestamp
    current_index: float
    return_pct: float
    high_index: float | None
    low_index: float | None
    intraday_trend: float
    acceleration: float
    program_flow_strength: float
    foreign_institution_flow_strength: float

    def to_dict(self) -> dict[str, Any]:
        return model_to_dict(self)


@dataclass
class SectorScore:
    sector_code: str
    sector_name: str
    score: float
    factors: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return model_to_dict(self)


@dataclass
class StockCandidate:
    symbol: str
    name: str
    sector_code: str | None
    sector_name: str | None
    return_pct: float
    volume_rank_score: float
    trade_strength: float
    high_proximity: float
    block_trade_intensity: float
    raw_factors: dict[str, Any]
    dynamic_sector_code: str | None = None
    dynamic_sector_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return model_to_dict(self)


@dataclass
class StockSnapshot:
    symbol: str
    name: str
    as_of: pd.Timestamp
    price: float
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    value_traded: float | None
    vwap: float | None
    intraday_trend_strength: float
    high_proximity: float
    foreign_institution_flow: float
    sector_score: float
    extra: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return model_to_dict(self)


@dataclass
class StockScore:
    symbol: str
    name: str
    sector_code: str | None
    sector_name: str | None
    score: float
    factors: dict[str, Any]
    passed_filters: bool
    reject_reason: str

    def to_dict(self) -> dict[str, Any]:
        return model_to_dict(self)


@dataclass
class SectorEvaluationResult:
    """고정/동적 섹터 공통 평가 결과 (메트릭 + 멤버)."""

    sector_code: str
    sector_name: str
    kind: str
    member_count: int
    members: list[str]
    score: float
    factors: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return model_to_dict(self)

    def to_sector_score(self) -> SectorScore:
        fac = dict(self.factors)
        fac["sector_kind"] = self.kind
        fac["member_count"] = self.member_count
        return SectorScore(sector_code=self.sector_code, sector_name=self.sector_name, score=float(self.score), factors=fac)


@dataclass
class ScanResult:
    as_of: pd.Timestamp
    top_sectors: list[SectorScore]
    leaders_by_sector: dict[str, list[StockScore]]
    all_sector_scores: list[SectorScore]
    all_stock_scores: list[StockScore]
    fixed_sector_results: list[SectorEvaluationResult] = field(default_factory=list)
    dynamic_sector_results: list[SectorEvaluationResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return model_to_dict(self)


def build_leader_sector_display_map(result: ScanResult) -> dict[str, str]:
    """leader_sector_key(F:015, DYN_3, 001 등) → 표시용 섹터명."""
    m: dict[str, str] = {}
    for ev in result.fixed_sector_results:
        k = str(ev.sector_code).strip()
        nm = str(ev.sector_name or "").strip()
        if k and nm:
            m[k] = nm
    for ev in result.dynamic_sector_results:
        k = str(ev.sector_code).strip()
        nm = str(ev.sector_name or "").strip()
        if k and nm:
            m[k] = nm
    for s in result.all_sector_scores:
        k = str(s.sector_code).strip()
        nm = str(s.sector_name or "").strip()
        if k and nm:
            m.setdefault(k, nm)
    for s in result.top_sectors:
        k = str(s.sector_code).strip()
        nm = str(s.sector_name or "").strip()
        if k and nm:
            m.setdefault(k, nm)
    return m


def format_leader_sector_heading(leader_key: str, display_map: dict[str, str]) -> str:
    key = str(leader_key).strip()
    name = (display_map.get(key) or "").strip()
    if not name:
        return key
    if name == key:
        return key
    if key in name:
        return name
    return f"{name} ({key})"
