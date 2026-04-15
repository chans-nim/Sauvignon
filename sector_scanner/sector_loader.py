"""업종/섹터 스냅샷 로더."""

from __future__ import annotations

import logging
from typing import Any, Protocol

import pandas as pd

from .models import SectorSnapshot

logger = logging.getLogger(__name__)


class _SectorDataClient(Protocol):
    def fetch_sector_current_index(self) -> list[dict[str, Any]]: ...
    def fetch_sector_intraday_index(self) -> list[dict[str, Any]]: ...
    def fetch_program_flow(self) -> list[dict[str, Any]]: ...
    def fetch_foreign_institution_flow(self) -> list[dict[str, Any]]: ...


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _merge_by_sector(
    current: list[dict[str, Any]],
    intraday: list[dict[str, Any]],
    program_by_sector: dict[str, float],
    foreign_by_sector: dict[str, float],
    tz: str,
    use_program: bool,
    use_foreign: bool,
) -> list[SectorSnapshot]:
    intra_map = {str(r.get("sector_code", "")).strip(): r for r in intraday if r.get("sector_code")}
    as_of = pd.Timestamp.now(tz=tz)
    snaps: list[SectorSnapshot] = []
    for row in current:
        code = str(row.get("sector_code", "")).strip()
        if not code:
            continue
        name = str(row.get("sector_name", code)).strip()
        ir = intra_map.get(code, {})
        ret = _safe_float(row.get("return_pct"))
        last_bar = _safe_float(ir.get("last_bar_return_pct"))
        intra_chg = _safe_float(ir.get("intraday_change_pct"))
        intraday_trend = intra_chg if intra_chg != 0.0 else last_bar
        acceleration = last_bar - intra_chg * 0.5
        prog = program_by_sector.get(code, 0.0) if use_program else 0.0
        frn = foreign_by_sector.get(code, 0.0) if use_foreign else 0.0
        hi = row.get("high_index")
        lo = row.get("low_index")
        snaps.append(
            SectorSnapshot(
                sector_code=code,
                sector_name=name,
                as_of=as_of,
                current_index=_safe_float(row.get("current_index")),
                return_pct=ret,
                high_index=float(hi) if hi is not None else None,
                low_index=float(lo) if lo is not None else None,
                intraday_trend=float(intraday_trend),
                acceleration=float(acceleration),
                program_flow_strength=float(prog),
                foreign_institution_flow_strength=float(frn),
            )
        )
    dedup: dict[str, SectorSnapshot] = {s.sector_code: s for s in snaps}
    return list(dedup.values())


class SectorLoader:
    """client에서 업종 데이터를 가져와 SectorSnapshot으로 정규화."""

    def __init__(self, client: _SectorDataClient, logger_: logging.Logger | None = None) -> None:
        self._client = client
        self._log = logger_ or logger

    def load_sector_snapshots(
        self,
        *,
        timezone: str = "Asia/Seoul",
        use_program_flow: bool = True,
        use_foreign_institution_flow: bool = True,
    ) -> list[SectorSnapshot]:
        try:
            cur = self._client.fetch_sector_current_index()
        except Exception as e:
            self._log.warning("fetch_sector_current_index failed, using empty: %s", e)
            cur = []
        try:
            intra = self._client.fetch_sector_intraday_index()
        except Exception as e:
            self._log.warning("fetch_sector_intraday_index failed, using empty: %s", e)
            intra = []

        program_by_sector: dict[str, float] = {}
        foreign_by_sector: dict[str, float] = {}
        if use_program_flow:
            try:
                for r in self._client.fetch_program_flow():
                    sc = str(r.get("sector_code", "")).strip()
                    if sc:
                        program_by_sector[sc] = _safe_float(r.get("program_net_strength"))
            except Exception as e:
                self._log.warning("fetch_program_flow (sector) skipped: %s", e)
        if use_foreign_institution_flow:
            try:
                for r in self._client.fetch_foreign_institution_flow():
                    sc = str(r.get("sector_code", "")).strip()
                    if sc:
                        foreign_by_sector[sc] = _safe_float(
                            r.get("foreign_net_strength")
                        ) + 0.5 * _safe_float(r.get("institution_net_strength"))
            except Exception as e:
                self._log.warning("fetch_foreign_institution_flow (sector) skipped: %s", e)

        return _merge_by_sector(
            cur,
            intra,
            program_by_sector,
            foreign_by_sector,
            timezone,
            use_program_flow,
            use_foreign_institution_flow,
        )
