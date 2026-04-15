"""대형 유니버스 로드·정규화·유동성 필터·목표 크기 압축."""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class _UniverseClient(Protocol):
    def fetch_universe_rows(self) -> list[dict[str, Any]]: ...


class UniverseLoader:
    """
    KOSPI200+KOSDAQ150 수준을 염두에 둔 유니버스 파이프라인.

    ``load_universe`` → ``normalize_universe`` → ``filter_liquid_universe`` 후
    ``target_size`` 로 상위 유동성 종목만 남긴다.
    """

    def __init__(self, client: _UniverseClient, logger_: logging.Logger | None = None) -> None:
        self._client = client
        self._log = logger_ or logger

    def load_universe(self) -> list[dict[str, Any]]:
        try:
            rows = self._client.fetch_universe_rows()
        except Exception as e:
            self._log.warning("fetch_universe_rows failed: %s", e)
            return []
        if not isinstance(rows, list):
            return []
        return [r for r in rows if isinstance(r, dict)]

    def normalize_universe(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for r in rows:
            sym = str(r.get("symbol", "")).strip()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            item = dict(r)
            item["symbol"] = sym
            item["name"] = str(item.get("name", sym) or sym).strip()
            item["market"] = str(item.get("market", "KOSPI")).strip().upper()
            try:
                item["value_traded"] = float(item.get("value_traded", 0.0) or 0.0)
            except (TypeError, ValueError):
                item["value_traded"] = 0.0
            out.append(item)
        return out

    def filter_liquid_universe(
        self,
        rows: list[dict[str, Any]],
        *,
        min_value_traded: float,
    ) -> list[dict[str, Any]]:
        m = float(min_value_traded)
        return [r for r in rows if float(r.get("value_traded", 0.0) or 0.0) >= m]

    def build_symbol_list(
        self,
        rows: list[dict[str, Any]],
        *,
        target_size: int,
        min_value_traded: float,
    ) -> list[str]:
        norm = self.normalize_universe(rows)
        liq = self.filter_liquid_universe(norm, min_value_traded=min_value_traded)
        liq.sort(key=lambda x: float(x.get("value_traded", 0.0) or 0.0), reverse=True)
        cap = max(1, int(target_size))
        return [str(r["symbol"]) for r in liq[:cap]]
