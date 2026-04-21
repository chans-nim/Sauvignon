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
        return float(str(x).replace(",", ""))
    except Exception:
        return default


def _pick(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
        uk = str(k).upper()
        if uk in d and d[uk] not in (None, ""):
            return d[uk]
    return default


def _normalize_pct(v: Any) -> float:
    x = _safe_float(v, 0.0)
    return x / 100.0 if abs(x) > 1.0 else x


class SectorLoader:
    """client에서 업종 데이터를 가져와 SectorSnapshot으로 정규화."""

    def __init__(self, client: _SectorDataClient, logger_: logging.Logger | None = None) -> None:
        self._client = client
        self._log = logger_ or logger

    def _normalize_sector_current_rows(self, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            sector_code = str(
                _pick(row, "sector_code", "bstp_cls_code", "bstp_lclscd", "upcode", "idx_shrn_iscd", "code", default="")
            ).strip()
            sector_name = str(
                _pick(row, "sector_name", "bstp_cls_name", "bstp_kor_isnm", "name", "hts_kor_isnm", "idx_name", default="")
            ).strip()
            if not sector_code and not sector_name:
                continue
            current_index = _safe_float(
                _pick(row, "current_index", "bstp_nmix_prpr", "idx_indx", "idx_clpr", "price", "close", default=0.0)
            )
            return_pct = _normalize_pct(_pick(row, "return_pct", "prdy_ctrt", "chg_rate", "fluc_rt", default=0.0))
            high_index = _safe_float(_pick(row, "high_index", "bstp_hgpr", "idx_hgpr", "high", default=0.0))
            low_index = _safe_float(_pick(row, "low_index", "bstp_lwpr", "idx_lwpr", "low", default=0.0))
            key = sector_code or sector_name
            out[key] = {
                "sector_code": sector_code or sector_name,
                "sector_name": sector_name or sector_code,
                "current_index": current_index,
                "return_pct": return_pct,
                "high_index": high_index if high_index > 0 else None,
                "low_index": low_index if low_index > 0 else None,
            }
        return out

    def _build_intraday_map(self, rows: list[dict[str, Any]]) -> dict[str, list[float]]:
        out: dict[str, list[float]] = {}
        for row in rows:
            sector_code = str(
                _pick(row, "sector_code", "bstp_cls_code", "bstp_lclscd", "upcode", "idx_shrn_iscd", "code", default="")
            ).strip()
            price = _safe_float(_pick(row, "current_index", "close", "price", "stck_prpr", "idx_clpr", default=0.0))
            if not sector_code or price <= 0:
                continue
            out.setdefault(sector_code, []).append(price)
        return out

    def _trend_and_acceleration(self, series: list[float]) -> tuple[float, float]:
        if len(series) < 2:
            return 0.0, 0.0
        s = pd.Series(series, dtype="float64")
        base = s.iloc[0]
        if base <= 0:
            return 0.0, 0.0
        trend = float((s.iloc[-1] / base) - 1.0)
        mid = max(1, len(s) // 2)
        first = s.iloc[:mid]
        second = s.iloc[mid:]
        accel = 0.0
        if len(first) > 0 and len(second) > 0 and first.iloc[0] > 0 and second.iloc[0] > 0:
            accel = float((second.iloc[-1] / second.iloc[0] - 1.0) - (first.iloc[-1] / first.iloc[0] - 1.0))
        return trend, accel

    def _build_program_flow_map(self, rows: list[dict[str, Any]]) -> dict[str, float]:
        out: dict[str, float] = {}
        for row in rows:
            sector_code = str(_pick(row, "sector_code", "bstp_cls_code", "upcode", "code", default="")).strip()
            if not sector_code:
                continue
            out[sector_code] = _safe_float(
                _pick(row, "program_flow_strength", "program_net_strength", "prsm_nmix_rate", "net_buy_strength", default=0.0)
            )
        return out

    def _build_foreign_flow_map(self, rows: list[dict[str, Any]]) -> dict[str, float]:
        out: dict[str, float] = {}
        for row in rows:
            sector_code = str(_pick(row, "sector_code", "bstp_cls_code", "upcode", "code", default="")).strip()
            if not sector_code:
                continue
            foreign_strength = _safe_float(
                _pick(row, "foreign_net_strength", "frgn_ntby_qty", "frgn_ntby_tr_pbmn", default=0.0)
            )
            institution_strength = _safe_float(
                _pick(row, "institution_net_strength", "orgn_ntby_qty", "orgnt_ntby_tr_pbmn", default=0.0)
            )
            out[sector_code] = foreign_strength + 0.5 * institution_strength
        return out

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
                program_by_sector = self._build_program_flow_map(self._client.fetch_program_flow())
            except Exception as e:
                self._log.warning("fetch_program_flow (sector) skipped: %s", e)
        if use_foreign_institution_flow:
            try:
                foreign_by_sector = self._build_foreign_flow_map(self._client.fetch_foreign_institution_flow())
            except Exception as e:
                self._log.warning("fetch_foreign_institution_flow (sector) skipped: %s", e)

        current_map = self._normalize_sector_current_rows(cur)
        intraday_map = self._build_intraday_map(intra)
        snapshots: list[SectorSnapshot] = []
        as_of = pd.Timestamp.now(tz=timezone)
        for _, meta in current_map.items():
            sector_code = str(meta["sector_code"]).strip()
            sector_name = str(meta["sector_name"]).strip()
            trend, accel = self._trend_and_acceleration(intraday_map.get(sector_code, []))
            snapshots.append(
                SectorSnapshot(
                    sector_code=sector_code,
                    sector_name=sector_name,
                    as_of=as_of,
                    current_index=float(meta["current_index"]),
                    return_pct=float(meta["return_pct"]),
                    high_index=meta["high_index"],
                    low_index=meta["low_index"],
                    intraday_trend=float(trend),
                    acceleration=float(accel),
                    program_flow_strength=float(program_by_sector.get(sector_code, 0.0)),
                    foreign_institution_flow_strength=float(foreign_by_sector.get(sector_code, 0.0)),
                )
            )
        return snapshots
