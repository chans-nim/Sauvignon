"""Sector and stock scoring engine."""

from __future__ import annotations

from typing import Sequence

import numpy as np
import pandas as pd

from .models import SectorEvaluationResult, SectorScore, SectorSnapshot, StockCandidate, StockScore, StockSnapshot
from .scanner_config import ScannerConfig
from .sector_metrics import (
    calc_breadth,
    calc_coherence,
    calc_concentration,
    calc_persistence,
    calc_relative_return,
)
from .sector_validator import SectorValidator


def _min_max_norm(values: Sequence[float]) -> list[float]:
    arr = np.array([float(v) for v in values], dtype=float)
    if arr.size == 0:
        return []
    lo = float(np.nanmin(arr))
    hi = float(np.nanmax(arr))
    if hi - lo < 1e-12:
        return [0.5] * len(arr)
    return [float((x - lo) / (hi - lo)) for x in arr]


def _sector_high_proximity(s: SectorSnapshot) -> float:
    hi = s.high_index
    lo = s.low_index
    cur = s.current_index
    if hi is None or lo is None or hi <= lo:
        return max(0.0, min(1.0, 0.5 + float(s.return_pct)))
    return max(0.0, min(1.0, (cur - lo) / (hi - lo)))


class ScoreEngine:
    """Weighted sum of factors scaled to 0–100."""

    def score_sectors(self, sectors: list[SectorSnapshot]) -> list[SectorScore]:
        if not sectors:
            return []
        rets = [float(s.return_pct) for s in sectors]
        trends = [float(s.intraday_trend) for s in sectors]
        prox = [_sector_high_proximity(s) for s in sectors]
        accs = [float(s.acceleration) for s in sectors]
        progs = [float(s.program_flow_strength) for s in sectors]
        frns = [float(s.foreign_institution_flow_strength) for s in sectors]

        nr = _min_max_norm(rets)
        nt = _min_max_norm(trends)
        np_ = _min_max_norm(prox)
        na = _min_max_norm(accs)
        npr = _min_max_norm(progs)
        nf = _min_max_norm(frns)

        out: list[SectorScore] = []
        for i, s in enumerate(sectors):
            contrib = {
                "return_pct": 0.35 * nr[i],
                "intraday_trend": 0.20 * nt[i],
                "high_proximity": 0.15 * np_[i],
                "acceleration": 0.10 * na[i],
                "program_flow": 0.10 * npr[i],
                "foreign_institution": 0.10 * nf[i],
            }
            raw = sum(contrib.values())
            score = round(100.0 * max(0.0, min(1.0, raw)), 4)
            out.append(
                SectorScore(
                    sector_code=s.sector_code,
                    sector_name=s.sector_name,
                    score=score,
                    factors={**{k: round(v * 100.0, 4) for k, v in contrib.items()}, "raw_weighted": round(raw, 6)},
                )
            )
        return out

    def evaluate_sector_group(
        self,
        returns: pd.DataFrame,
        members: list[str],
        sector_code: str,
        sector_name: str,
        kind: str,
        cfg: ScannerConfig,
        validator: SectorValidator,
    ) -> SectorEvaluationResult | None:
        """Return evaluation or None if validation fails."""
        mem = [m for m in members if m in returns.columns]
        if len(mem) < 2:
            return None
        market_returns = returns.mean(axis=1)
        mets = {
            "coherence": calc_coherence(returns, mem),
            "breadth": calc_breadth(returns, mem),
            "persistence": calc_persistence(returns, mem),
            "concentration": calc_concentration(returns, mem),
            "relative_return_norm": calc_relative_return(returns, mem, market_returns),
        }
        ok, reason = validator.ok(mets, member_count=len(mem))
        raw = (
            float(cfg.sector_w_coherence) * mets["coherence"]
            + float(cfg.sector_w_breadth) * mets["breadth"]
            + float(cfg.sector_w_persistence) * mets["persistence"]
            + float(cfg.sector_w_relative_return) * mets["relative_return_norm"]
            - float(cfg.sector_w_concentration) * mets["concentration"]
        )
        score = round(100.0 * max(0.0, min(1.0, raw)), 4)
        factors = {
            **{k: round(float(v), 6) for k, v in mets.items()},
            "weighted_raw": round(float(raw), 6),
            "validated": bool(ok),
            "reject_reason": reason,
        }
        if not ok:
            return None
        return SectorEvaluationResult(
            sector_code=sector_code,
            sector_name=sector_name,
            kind=kind,
            member_count=len(mem),
            members=mem[:30],
            score=score,
            factors=factors,
        )

    def score_stocks(
        self,
        candidates: list[StockCandidate],
        snapshots: list[StockSnapshot],
        sector_scores: list[SectorScore],
        *,
        blend_config: ScannerConfig | None = None,
        breadth_by_key: dict[str, float] | None = None,
        eval_score_by_key: dict[str, float] | None = None,
        concentration_by_key: dict[str, float] | None = None,
    ) -> list[StockScore]:
        snap_by_sym = {s.symbol: s for s in snapshots}

        if not candidates:
            return []

        rets = [float(c.return_pct) for c in candidates]
        vols = [float(c.volume_rank_score) for c in candidates]
        trades = [float(c.trade_strength) for c in candidates]
        cand_hi = [float(c.high_proximity) for c in candidates]
        blocks = [float(c.block_trade_intensity) for c in candidates]

        nr = _min_max_norm(rets)
        nv = _min_max_norm(vols)
        nt = _min_max_norm(trades)
        nh = _min_max_norm(cand_hi)
        nb = _min_max_norm(blocks)

        it_list: list[float] = []
        hi_snap_list: list[float] = []
        fr_list: list[float] = []
        sec_s_list: list[float] = []
        for i, c in enumerate(candidates):
            sn = snap_by_sym.get(c.symbol)
            if sn is None:
                it_list.append(0.0)
                hi_snap_list.append(0.0)
                fr_list.append(0.0)
                sec_s_list.append(0.0)
            else:
                it_list.append(float(sn.intraday_trend_strength))
                hi_snap_list.append(float(sn.high_proximity))
                fr_list.append(float(sn.foreign_institution_flow))
                sec_s_list.append(float(sn.sector_score))
        nit = _min_max_norm(it_list)
        nhs = _min_max_norm(hi_snap_list)
        nfr = _min_max_norm(fr_list)
        nsec = _min_max_norm(sec_s_list)

        out: list[StockScore] = []
        for i, c in enumerate(candidates):
            contrib = {
                "return_pct": 0.20 * nr[i],
                "volume_rank": 0.20 * nv[i],
                "trade_strength": 0.15 * nt[i],
                "intraday_trend": 0.15 * nit[i],
                "high_proximity": 0.10 * max(nh[i], nhs[i]),
                "sector_score": 0.10 * nsec[i],
                "foreign_institution": 0.05 * nfr[i],
                "block_intensity": 0.05 * nb[i],
            }
            raw = sum(contrib.values())
            score = round(100.0 * max(0.0, min(1.0, raw)), 4)
            fac = {**{k: round(v * 100.0, 4) for k, v in contrib.items()}, "raw_weighted": round(raw, 6)}
            if blend_config is not None and eval_score_by_key:
                keys: list[str] = []
                if c.sector_code:
                    keys.append(f"F:{str(c.sector_code).strip()}")
                if c.dynamic_sector_code:
                    keys.append(str(c.dynamic_sector_code).strip())
                ev = max((eval_score_by_key.get(k, 0.0) for k in keys), default=0.0) if keys else 0.0
                br = max((breadth_by_key or {}).get(k, 0.0) for k in keys) if keys else 0.0
                conc = max((concentration_by_key or {}).get(k, 0.0) for k in keys) if keys else 0.0
                add_s = float(blend_config.stock_alpha_sector) * float(ev) / 100.0 * 28.0
                add_b = float(blend_config.stock_beta_breadth) * float(br) * 18.0
                pen = float(blend_config.stock_concentration_penalty_scale) * max(0.0, float(conc) - 0.42) ** 2
                score2 = max(0.0, min(100.0, float(score) + add_s + add_b - pen))
                score = round(score2, 4)
                fac.update(
                    {
                        "base_stock_score": round(100.0 * max(0.0, min(1.0, raw)), 4),
                        "blend_add_sector": round(add_s, 4),
                        "blend_add_breadth": round(add_b, 4),
                        "blend_penalty_concentration": round(pen, 4),
                        "sector_eval_used": round(ev, 4),
                        "breadth_support": round(br, 6),
                        "sector_concentration": round(conc, 6),
                    }
                )
            out.append(
                StockScore(
                    symbol=c.symbol,
                    name=c.name,
                    sector_code=c.sector_code,
                    sector_name=c.sector_name,
                    score=score,
                    factors=fac,
                    passed_filters=True,
                    reject_reason="",
                )
            )
        return out
