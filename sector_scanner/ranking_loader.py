"""순위분석 API 결과 → StockCandidate 정규화."""

from __future__ import annotations

import logging
from typing import Any, Protocol

from .models import StockCandidate
from .sector_mapper import SectorMapper

logger = logging.getLogger(__name__)


class _RankingClient(Protocol):
    def fetch_ranking_volume(self) -> list[dict[str, Any]]: ...
    def fetch_ranking_return(self) -> list[dict[str, Any]]: ...
    def fetch_ranking_trade_strength(self) -> list[dict[str, Any]]: ...
    def fetch_ranking_near_high(self) -> list[dict[str, Any]]: ...
    def fetch_ranking_block_trades(self) -> list[dict[str, Any]]: ...


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _norm_rank(rank: Any, n: int) -> float:
    """순위를 0~1 점수로 (1위에 가까울수록 높음)."""
    try:
        r = int(rank)
    except (TypeError, ValueError):
        return 0.5
    if n <= 0:
        return 0.5
    return max(0.0, min(1.0, 1.0 - (r - 1) / float(max(1, n))))


class RankingLoader:
    def __init__(self, client: _RankingClient, sector_mapper: SectorMapper, logger_: logging.Logger | None = None) -> None:
        self._client = client
        self._mapper = sector_mapper
        self._log = logger_ or logger

    def load_candidates(
        self,
        *,
        allowed_symbols: set[str] | None = None,
        dynamic_sector_by_symbol: dict[str, tuple[str, str]] | None = None,
    ) -> list[StockCandidate]:
        buckets: dict[str, dict[str, Any]] = {}

        def ingest(rows: list[dict[str, Any]], source: str) -> None:
            n = max(1, len(rows))
            for row in rows:
                sym = str(row.get("symbol", "")).strip()
                if not sym:
                    continue
                if allowed_symbols is not None and sym not in allowed_symbols:
                    continue
                name = str(row.get("name", sym)).strip() or sym
                slot = buckets.setdefault(
                    sym,
                    {
                        "symbol": sym,
                        "name": name,
                        "sector_code": row.get("sector_code"),
                        "sector_name": row.get("sector_name"),
                        "return_pct": 0.0,
                        "volume_rank_score": 0.0,
                        "trade_strength": 0.0,
                        "high_proximity": 0.0,
                        "block_trade_intensity": 0.0,
                        "raw_factors": {},
                    },
                )
                slot["name"] = name or slot["name"]
                rf = slot["raw_factors"]
                rf[source] = dict(row)
                m = _safe_float(row.get("metric"))
                rk = row.get("rank")
                if source == "rank_volume":
                    slot["volume_rank_score"] = max(slot["volume_rank_score"], _norm_rank(rk, n))
                elif source == "rank_return":
                    slot["return_pct"] = max(slot["return_pct"], m)
                elif source == "rank_trade_strength":
                    slot["trade_strength"] = max(slot["trade_strength"], min(1.0, max(0.0, m)))
                elif source == "rank_near_high":
                    slot["high_proximity"] = max(slot["high_proximity"], min(1.0, max(0.0, m)))
                elif source == "rank_block":
                    slot["block_trade_intensity"] = max(slot["block_trade_intensity"], min(1.0, max(0.0, m)))

        try:
            ingest(self._client.fetch_ranking_volume(), "rank_volume")
        except Exception as e:
            self._log.warning("fetch_ranking_volume failed: %s", e)
        try:
            ingest(self._client.fetch_ranking_return(), "rank_return")
        except Exception as e:
            self._log.warning("fetch_ranking_return failed: %s", e)
        try:
            ingest(self._client.fetch_ranking_trade_strength(), "rank_trade_strength")
        except Exception as e:
            self._log.warning("fetch_ranking_trade_strength failed: %s", e)
        try:
            ingest(self._client.fetch_ranking_near_high(), "rank_near_high")
        except Exception as e:
            self._log.warning("fetch_ranking_near_high failed: %s", e)
        try:
            ingest(self._client.fetch_ranking_block_trades(), "rank_block")
        except Exception as e:
            self._log.warning("fetch_ranking_block_trades failed: %s", e)

        merged_rows = [dict(v) for v in buckets.values()]
        attached = self._mapper.attach_sector_info(merged_rows)

        candidates: list[StockCandidate] = []
        for row in attached:
            sym = str(row["symbol"])
            name = str(row.get("name") or sym)
            sc = row.get("sector_code")
            sn = row.get("sector_name")
            dyn = (dynamic_sector_by_symbol or {}).get(sym)
            dc = dyn[0] if dyn else None
            dn = dyn[1] if dyn else None
            candidates.append(
                StockCandidate(
                    symbol=sym,
                    name=name,
                    sector_code=str(sc).strip() if sc else None,
                    sector_name=str(sn).strip() if sn else None,
                    return_pct=_safe_float(row.get("return_pct")),
                    volume_rank_score=_safe_float(row.get("volume_rank_score")),
                    trade_strength=_safe_float(row.get("trade_strength")),
                    high_proximity=_safe_float(row.get("high_proximity")),
                    block_trade_intensity=_safe_float(row.get("block_trade_intensity")),
                    raw_factors=dict(row.get("raw_factors") or {}),
                    dynamic_sector_code=dc,
                    dynamic_sector_name=dn,
                )
            )
        return candidates
