"""
스캔 결과 → 백테스트 / 라이브 엔진 후보 유니버스 export.

공용 모델과 JSON·심볼 리스트·LiveTradingConfig 보조 생성기를 제공한다.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .models import ScanResult, StockScore, model_to_dict


@dataclass
class UniverseCandidate:
    """백테스트·라이브가 공통으로 소비할 수 있는 후보 1건."""

    symbol: str
    name: str
    sector_code: str | None
    sector_name: str | None
    scanner_score: float
    passed_filters: bool
    rank_in_universe: int
    source: str = "sector_scanner"
    as_of_iso: str = ""
    reject_reason: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dict(asdict(self))


@dataclass
class ScannerUniversePackage:
    """한 번의 스캔에서 추출한 유니버스 묶음."""

    as_of_iso: str
    candidates: list[UniverseCandidate]
    symbol_list: list[str]
    symbol_sector: dict[str, str]
    leaders_by_sector: dict[str, list[str]]
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of_iso": self.as_of_iso,
            "candidates": [c.to_dict() for c in self.candidates],
            "symbol_list": list(self.symbol_list),
            "symbol_sector": dict(self.symbol_sector),
            "leaders_by_sector": {k: list(v) for k, v in self.leaders_by_sector.items()},
            "meta": dict(self.meta),
        }


def apply_realtime_overlay_to_scores(
    all_scores: list[StockScore],
    runtime: dict[str, Any],
) -> list[StockScore]:
    """runtime_state의 ``stock_scores_realtime``으로 동일 심볼 점수·factors를 덮어쓴다."""
    raw = runtime.get("stock_scores_realtime")
    if not isinstance(raw, list):
        return list(all_scores)
    rt_map: dict[str, StockScore] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        ss = _stock_score_from_dict(item)
        if ss is not None:
            rt_map[ss.symbol] = ss
    if not rt_map:
        return list(all_scores)
    out: list[StockScore] = []
    for s in all_scores:
        out.append(rt_map.get(s.symbol, s))
    return out


def _stock_score_from_dict(d: dict[str, Any]) -> StockScore | None:
    try:
        return StockScore(
            symbol=str(d["symbol"]),
            name=str(d.get("name", d["symbol"])),
            sector_code=d.get("sector_code"),
            sector_name=d.get("sector_name"),
            score=float(d.get("score", 0.0)),
            factors=dict(d.get("factors") or {}),
            passed_filters=bool(d.get("passed_filters", False)),
            reject_reason=str(d.get("reject_reason", "") or ""),
        )
    except (KeyError, TypeError, ValueError):
        return None


def leaders_from_runtime_state(runtime: dict[str, Any]) -> dict[str, list[StockScore]] | None:
    """runtime_state.json에 저장된 실시간 재랭킹 리더를 복원."""
    raw = runtime.get("leaders_by_sector_realtime")
    if not isinstance(raw, dict):
        return None
    out: dict[str, list[StockScore]] = {}
    for sec, rows in raw.items():
        if not isinstance(rows, list):
            continue
        parsed: list[StockScore] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            ss = _stock_score_from_dict(item)
            if ss is not None:
                parsed.append(ss)
        out[str(sec)] = parsed
    return out if out else None


def build_universe_package(
    result: ScanResult,
    *,
    stock_scores_override: list[StockScore] | None = None,
    leaders_override: dict[str, list[StockScore]] | None = None,
    only_passed: bool = True,
    max_symbols: int | None = None,
    prefer_leaders_only: bool = False,
) -> ScannerUniversePackage:
    """
    ScanResult에서 유니버스 패키지 생성.

    Parameters
    ----------
    stock_scores_override
        실시간 보정된 ``StockScore`` 리스트가 있으면 랭킹·후보 풀에 사용.
    leaders_override
        실시간 재계산된 leaders가 있으면 이를 우선 사용.
    only_passed
        True면 필터 통과 종목만 후보에 넣는다.
    prefer_leaders_only
        True면 leaders(또는 override)에 등장한 심볼만 후보로 삼는다.
    """
    as_of_iso = result.as_of.isoformat() if hasattr(result.as_of, "isoformat") else str(result.as_of)

    base_scores = list(stock_scores_override) if stock_scores_override is not None else list(result.all_stock_scores)

    leaders = leaders_override if leaders_override is not None else result.leaders_by_sector
    leader_syms: set[str] = set()
    leaders_symbols_map: dict[str, list[str]] = {}
    for sec, rows in leaders.items():
        syms = [s.symbol for s in rows]
        leaders_symbols_map[str(sec)] = syms
        leader_syms.update(syms)

    pool: list[StockScore] = []
    if prefer_leaders_only:
        for sec, rows in leaders.items():
            pool.extend(rows)
    else:
        pool = list(base_scores)

    if only_passed:
        pool = [s for s in pool if s.passed_filters]

    if prefer_leaders_only:
        pool = [s for s in pool if s.symbol in leader_syms]

    # 점수 내림차순, 중복 심볼 제거(첫 등장 유지)
    pool.sort(key=lambda x: x.score, reverse=True)
    seen: set[str] = set()
    ordered: list[StockScore] = []
    for s in pool:
        if s.symbol in seen:
            continue
        seen.add(s.symbol)
        ordered.append(s)

    if max_symbols is not None and max_symbols > 0:
        ordered = ordered[:max_symbols]

    candidates: list[UniverseCandidate] = []
    sym_sector: dict[str, str] = {}
    for i, s in enumerate(ordered, start=1):
        sec_name = s.sector_name or ""
        if s.sector_code:
            sym_sector[s.symbol] = sec_name or str(s.sector_code)
        candidates.append(
            UniverseCandidate(
                symbol=s.symbol,
                name=s.name,
                sector_code=s.sector_code,
                sector_name=s.sector_name,
                scanner_score=float(s.score),
                passed_filters=s.passed_filters,
                rank_in_universe=i,
                as_of_iso=as_of_iso,
                reject_reason=s.reject_reason,
                extra={"factors": dict(s.factors)},
            )
        )

    symbol_list = [c.symbol for c in candidates]

    return ScannerUniversePackage(
        as_of_iso=as_of_iso,
        candidates=candidates,
        symbol_list=symbol_list,
        symbol_sector=sym_sector,
        leaders_by_sector=leaders_symbols_map,
        meta={
            "top_sector_codes": [x.sector_code for x in result.top_sectors],
            "prefer_leaders_only": prefer_leaders_only,
            "only_passed": only_passed,
            "used_realtime_scores": stock_scores_override is not None,
            "used_realtime_leaders": leaders_override is not None,
        },
    )


def export_universe_json(package: ScannerUniversePackage, path: str | Path) -> Path:
    """유니버스 패키지를 JSON으로 저장."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(package.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def symbols_for_backtest(
    package: ScannerUniversePackage,
    *,
    market: str = "KOSPI",
) -> list[tuple[str, str]]:
    """
    ``silver_data.load_backtest_bundle`` 등에 넘길 (symbol, market) 스펙 리스트.
    """
    m = str(market).strip().upper()
    return [(s, m) for s in package.symbol_list]


def merge_live_symbol_maps(
    existing: dict[str, str],
    package: ScannerUniversePackage,
) -> dict[str, str]:
    """LiveTradingConfig.symbol_sector에 병합할 맵 (기존 값 우선 유지 가능)."""
    out = dict(existing)
    for sym, sec in package.symbol_sector.items():
        out.setdefault(sym, sec)
    return out


def attach_universe_to_live_config_symbols(
    symbols: list[str],
    symbol_sector: dict[str, str],
) -> tuple[list[str], dict[str, str]]:
    """
    라이브 설정에 그대로 넣을 (symbols, symbol_sector).

    호출측에서 ``LiveTradingConfig(..., symbols=..., symbol_sector=...)`` 로 복사본을 만들면 된다.
    """
    uniq: list[str] = []
    seen: set[str] = set()
    for s in symbols:
        s = str(s).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        uniq.append(s)
    sec = {k: symbol_sector[k] for k in uniq if k in symbol_sector}
    return uniq, sec
