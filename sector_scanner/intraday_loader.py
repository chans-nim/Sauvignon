"""Build StockSnapshot from live price and intraday bars."""

from __future__ import annotations

import logging
from typing import Any, Protocol

import pandas as pd

from .models import StockCandidate, StockSnapshot

logger = logging.getLogger(__name__)


class _IntradayClient(Protocol):
    def fetch_stock_price(self, symbol: str) -> dict[str, Any]: ...
    def fetch_stock_intraday_bars(self, symbol: str) -> list[dict[str, Any]]: ...
    def fetch_foreign_institution_flow(self) -> list[dict[str, Any]]: ...


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _compute_vwap(bars: list[dict[str, Any]]) -> float | None:
    if not bars:
        return None
    num = 0.0
    den = 0.0
    for b in bars:
        tp = (_safe_float(b.get("high")) + _safe_float(b.get("low")) + _safe_float(b.get("close"))) / 3.0
        v = _safe_float(b.get("volume"))
        if v > 0 and tp > 0:
            num += tp * v
            den += v
    if den <= 0:
        return None
    return num / den


def _intraday_trend_strength(bars: list[dict[str, Any]]) -> float:
    if len(bars) < 2:
        return 0.0
    c0 = _safe_float(bars[0].get("close"))
    c1 = _safe_float(bars[-1].get("close"))
    if c0 <= 0:
        return 0.0
    r = (c1 / c0) - 1.0
    return max(0.0, min(1.0, 0.5 + r * 25.0))


def _high_proximity_from_bars(bars: list[dict[str, Any]], last_close: float) -> float:
    if not bars or last_close <= 0:
        return 0.0
    highs = [_safe_float(b.get("high")) for b in bars if _safe_float(b.get("high")) > 0]
    if not highs:
        return 0.0
    day_high = max(highs)
    return max(0.0, min(1.0, last_close / day_high))


class IntradayLoader:
    def __init__(self, client: _IntradayClient, logger_: logging.Logger | None = None) -> None:
        self._client = client
        self._log = logger_ or logger
        self._foreign_flow_map: dict[str, float] | None = None

    def _build_foreign_flow_map(self) -> dict[str, float]:
        try:
            rows = self._client.fetch_foreign_institution_flow()
        except Exception as e:
            self._log.debug("fetch_foreign_institution_flow skipped in flow map: %s", e)
            return {}
        out: dict[str, float] = {}
        for r in rows:
            sym = str(r.get("symbol", "")).strip()
            if not sym:
                continue
            out[sym] = _safe_float(r.get("foreign_net_strength")) + 0.5 * _safe_float(r.get("institution_net_strength"))
        return out

    def _foreign_flow_for_symbol(self, symbol: str) -> float:
        if self._foreign_flow_map is None:
            self._foreign_flow_map = self._build_foreign_flow_map()
        return float(self._foreign_flow_map.get(str(symbol).strip(), 0.0))

    def load_snapshot(
        self,
        symbol: str,
        name: str,
        sector_code: str | None,
        sector_name: str | None,
        sector_score: float,
    ) -> StockSnapshot:
        extra: dict[str, Any] = {}
        try:
            px_row = self._client.fetch_stock_price(symbol)
        except Exception as e:
            self._log.warning("fetch_stock_price failed for %s: %s", symbol, e)
            px_row = {}
            extra["price_error"] = str(e)

        try:
            bars = self._client.fetch_stock_intraday_bars(symbol)
        except Exception as e:
            self._log.warning("fetch_stock_intraday_bars failed for %s: %s", symbol, e)
            bars = []
            extra["bars_error"] = str(e)

        price = _safe_float(px_row.get("price")) or _safe_float(px_row.get("close"))
        open_ = px_row.get("open")
        high = px_row.get("high")
        low = px_row.get("low")
        close = px_row.get("close")
        vol = px_row.get("volume")
        vt = px_row.get("value_traded")
        if (vt is None or _safe_float(vt) <= 0) and vol is not None and price > 0:
            vt = _safe_float(vol) * price

        vwap = _compute_vwap(bars)
        trend = _intraday_trend_strength(bars)
        hi_prox = _high_proximity_from_bars(bars, price if price > 0 else _safe_float(close))
        n_bars = len(bars)
        avg_vol = sum(_safe_float(b.get("volume")) for b in bars) / max(1, n_bars)
        extra["avg_bar_volume"] = avg_vol
        extra["bar_count"] = n_bars
        ff = self._foreign_flow_for_symbol(symbol)

        return StockSnapshot(
            symbol=symbol,
            name=name,
            as_of=pd.Timestamp.now(tz="Asia/Seoul"),
            price=float(price),
            open=float(open_) if open_ is not None else None,
            high=float(high) if high is not None else None,
            low=float(low) if low is not None else None,
            close=float(close) if close is not None else None,
            volume=float(vol) if vol is not None else None,
            value_traded=float(vt) if vt is not None else None,
            vwap=float(vwap) if vwap is not None else None,
            intraday_trend_strength=float(trend),
            high_proximity=float(hi_prox),
            foreign_institution_flow=float(ff),
            sector_score=float(sector_score),
            extra=extra,
        )

    def load_many(
        self,
        candidates: list[StockCandidate],
        sector_score_map: dict[str, float],
        *,
        per_symbol_sector_score: dict[str, float] | None = None,
    ) -> list[StockSnapshot]:
        prev = self._foreign_flow_map
        self._foreign_flow_map = self._build_foreign_flow_map()
        try:
            out: list[StockSnapshot] = []
            for c in candidates:
                if per_symbol_sector_score is not None and c.symbol in per_symbol_sector_score:
                    ss = float(per_symbol_sector_score[c.symbol])
                else:
                    sc = c.sector_code or ""
                    ss = float(sector_score_map.get(sc, 0.0))
                    if c.dynamic_sector_code:
                        ss = max(ss, float(sector_score_map.get(c.dynamic_sector_code, 0.0)))
                try:
                    out.append(self.load_snapshot(c.symbol, c.name, c.sector_code, c.sector_name, ss))
                except Exception as e:
                    self._log.warning("load_snapshot skip %s: %s", c.symbol, e)
            return out
        finally:
            self._foreign_flow_map = prev
