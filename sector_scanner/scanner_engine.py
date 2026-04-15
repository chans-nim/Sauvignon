"""스캐너 오케스트레이션."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Protocol

import pandas as pd

from .dynamic_sector_cluster import DynamicSectorCluster
from .filters import passes_filters
from .models import (
    ScanResult,
    SectorScore,
    StockCandidate,
    StockScore,
    StockSnapshot,
    build_leader_sector_display_map,
    model_to_dict,
)
from .realtime_tick_score import adjust_scores_with_ticks, merge_tick_buffer
from .scanner_config import ScannerConfig
from .scanner_state import ScannerStateStore
from .score_engine import ScoreEngine
from .sector_validator import SectorValidator
from .universe_loader import UniverseLoader


class _LoaderProtocol(Protocol):
    def load_sector_snapshots(self, **kwargs: Any) -> list[Any]: ...


class _RankingLoaderProtocol(Protocol):
    def load_candidates(
        self,
        *,
        allowed_symbols: set[str] | None = None,
        dynamic_sector_by_symbol: dict[str, tuple[str, str]] | None = None,
    ) -> list[StockCandidate]: ...


class _IntradayClientLike(Protocol):
    def fetch_stock_price(self, symbol: str) -> dict[str, Any]: ...
    def fetch_stock_intraday_bars(self, symbol: str) -> list[dict[str, Any]]: ...
    def fetch_foreign_institution_flow(self) -> list[dict[str, Any]]: ...
    def fetch_price_history_frame(self, symbols: list[str], *, days: int = 60) -> Any: ...


class _IntradayLoaderProtocol(Protocol):
    def load_many(
        self,
        candidates: list[StockCandidate],
        sector_score_map: dict[str, float],
        *,
        per_symbol_sector_score: dict[str, float] | None = None,
    ) -> list[StockSnapshot]: ...


class _WSProtocol(Protocol):
    def stream(self, symbols: list[str]): ...


logger = logging.getLogger(__name__)


class ScannerEngine:
    def __init__(
        self,
        config: ScannerConfig,
        sector_loader: _LoaderProtocol,
        ranking_loader: _RankingLoaderProtocol,
        intraday_loader: _IntradayLoaderProtocol,
        websocket_adapter: _WSProtocol,
        score_engine: ScoreEngine,
        state_store: ScannerStateStore,
        logger_: logging.Logger | None = None,
        *,
        universe_loader: UniverseLoader | None = None,
        clusterer: DynamicSectorCluster | None = None,
        data_client: _IntradayClientLike | None = None,
    ) -> None:
        self._cfg = config
        self._sector_loader = sector_loader
        self._ranking_loader = ranking_loader
        self._intraday_loader = intraday_loader
        self._ws = websocket_adapter
        self._score = score_engine
        self._state = state_store
        self._log = logger_ or logger
        self._snap_by_symbol: dict[str, StockSnapshot] = {}
        self._last_stock_scores: list[StockScore] = []
        self._universe_loader = universe_loader
        self._clusterer = clusterer
        self._data_client = data_client
        self._leader_sector_display: dict[str, str] = {}

    def pick_top_sectors(self, sector_scores: list[SectorScore]) -> list[SectorScore]:
        ranked = sorted(sector_scores, key=lambda s: s.score, reverse=True)
        return ranked[: int(self._cfg.top_sector_n)]

    def pick_leaders_by_sector(self, stock_scores: list[StockScore]) -> dict[str, list[StockScore]]:
        k = int(self._cfg.top_stock_k_per_sector)
        passed = [s for s in stock_scores if s.passed_filters]
        by_sec: dict[str, list[StockScore]] = {}
        for s in passed:
            key = str(s.factors.get("leader_sector_key") or s.sector_code or "UNKNOWN")
            by_sec.setdefault(key, []).append(s)
        out: dict[str, list[StockScore]] = {}
        for sec, rows in by_sec.items():
            rows_sorted = sorted(rows, key=lambda x: x.score, reverse=True)
            out[sec] = rows_sorted[:k]
        return out

    def scan_once(self) -> ScanResult:
        if (
            self._cfg.use_universe_pipeline
            and self._universe_loader is not None
            and self._clusterer is not None
            and self._data_client is not None
        ):
            try:
                return self._scan_once_universe()
            except Exception as e:
                self._log.exception("universe pipeline failed, legacy fallback: %s", e)
        return self._scan_once_legacy()

    def _scan_once_legacy(self) -> ScanResult:
        as_of = pd.Timestamp.now(tz=self._cfg.timezone)

        try:
            sectors = self._sector_loader.load_sector_snapshots(
                timezone=self._cfg.timezone,
                use_program_flow=self._cfg.use_program_flow,
                use_foreign_institution_flow=self._cfg.use_foreign_institution_flow,
            )
        except Exception as e:
            self._log.error("load_sector_snapshots aborted: %s", e)
            sectors = []

        sector_scores = self._score.score_sectors(sectors)
        top_sectors = self.pick_top_sectors(sector_scores)
        top_codes = {s.sector_code for s in top_sectors}

        try:
            candidates = self._ranking_loader.load_candidates()
        except Exception as e:
            self._log.error("load_candidates aborted: %s", e)
            candidates = []

        pool: list[StockCandidate] = []
        for c in candidates:
            if c.sector_code and c.sector_code not in top_codes:
                continue
            pool.append(c)
        if not pool:
            pool = candidates[: int(self._cfg.candidate_pool_limit)]
        pool = pool[: int(self._cfg.candidate_pool_limit)]

        sec_score_map = {s.sector_code: float(s.score) for s in sector_scores}

        try:
            snapshots = self._intraday_loader.load_many(pool, sec_score_map)
        except Exception as e:
            self._log.error("load_many snapshots aborted: %s", e)
            snapshots = []

        stock_scores = self._score.score_stocks(pool, snapshots, sector_scores)
        adjusted = self._apply_filters(pool, snapshots, stock_scores, sec_score_map)

        self._snap_by_symbol = {s.symbol: s for s in snapshots}
        self._last_stock_scores = list(adjusted)

        leaders = self.pick_leaders_by_sector(adjusted)
        result = ScanResult(
            as_of=as_of,
            top_sectors=top_sectors,
            leaders_by_sector=leaders,
            all_sector_scores=sector_scores,
            all_stock_scores=adjusted,
            fixed_sector_results=[],
            dynamic_sector_results=[],
        )
        self._save_result(result)
        return result

    def _scan_once_universe(self) -> ScanResult:
        assert self._universe_loader is not None and self._clusterer is not None and self._data_client is not None
        as_of = pd.Timestamp.now(tz=self._cfg.timezone)
        cfg = self._cfg
        ul = self._universe_loader
        cl = self._clusterer
        dc = self._data_client

        rows = ul.load_universe()
        norm = ul.normalize_universe(rows)
        sym_info = {str(r["symbol"]): r for r in norm}
        symbols = ul.build_symbol_list(
            norm,
            target_size=int(cfg.universe_target_size),
            min_value_traded=float(cfg.universe_min_value_traded),
        )
        if not symbols:
            self._log.warning("universe empty after filter — fallback legacy")
            return self._scan_once_legacy()

        try:
            returns = dc.fetch_price_history_frame(symbols, days=int(cfg.sector_metric_window_days))
        except Exception as e:
            self._log.error("fetch_price_history_frame failed: %s", e)
            returns = pd.DataFrame()

        if not isinstance(returns, pd.DataFrame) or returns.empty:
            self._log.warning("no return history — legacy fallback")
            return self._scan_once_legacy()

        cl.n_clusters = int(cfg.dynamic_n_clusters)
        cl.min_cluster_size = int(cfg.dynamic_min_cluster_size)
        cl.max_cluster_size = int(cfg.dynamic_max_cluster_size)
        cl.fit(returns)
        dyn_groups = cl.filter_cluster_sizes(cl.build_sector_groups())

        fixed_groups: dict[str, list[str]] = defaultdict(list)
        for s in symbols:
            info = sym_info.get(s, {})
            fc = info.get("sector_code")
            if fc:
                fixed_groups[f"F:{str(fc).strip()}"].append(s)

        validator = SectorValidator(cfg)
        fixed_results = []
        for code, mem in fixed_groups.items():
            if len(mem) < 2:
                continue
            nm = str(sym_info.get(mem[0], {}).get("sector_name", code))
            ev = self._score.evaluate_sector_group(returns, mem, code, nm, "fixed", cfg, validator)
            if ev is not None:
                fixed_results.append(ev)

        dynamic_results = []
        for dcode, mem in dyn_groups.items():
            if len(mem) < 2:
                continue
            ev = self._score.evaluate_sector_group(
                returns,
                mem,
                dcode,
                f"동적 {dcode}",
                "dynamic",
                cfg,
                validator,
            )
            if ev is not None:
                dynamic_results.append(ev)

        merged_scores = [e.to_sector_score() for e in fixed_results + dynamic_results]
        if not merged_scores:
            self._log.warning("no evaluated sectors — legacy fallback")
            return self._scan_once_legacy()

        top_sectors = self.pick_top_sectors(merged_scores)
        top_codes = {s.sector_code for s in top_sectors}

        dyn_symbol_map: dict[str, tuple[str, str]] = {}
        for dcode, mem in dyn_groups.items():
            for s in mem:
                dyn_symbol_map[str(s)] = (dcode, f"동적 {dcode}")

        try:
            candidates = self._ranking_loader.load_candidates(
                allowed_symbols=set(symbols),
                dynamic_sector_by_symbol=dyn_symbol_map,
            )
        except Exception as e:
            self._log.error("load_candidates aborted: %s", e)
            candidates = []

        pool: list[StockCandidate] = []
        for c in candidates:
            in_fixed = bool(c.sector_code and f"F:{c.sector_code}" in top_codes)
            in_dyn = bool(c.dynamic_sector_code and c.dynamic_sector_code in top_codes)
            if in_fixed or in_dyn:
                pool.append(c)
        if not pool:
            pool = candidates[: int(cfg.candidate_pool_limit)]
        pool = pool[: int(cfg.candidate_pool_limit)]

        breadth_by_key: dict[str, float] = {}
        conc_by_key: dict[str, float] = {}
        eval_by_key: dict[str, float] = {}
        for ev in fixed_results + dynamic_results:
            breadth_by_key[ev.sector_code] = float(ev.factors.get("breadth", 0.0))
            conc_by_key[ev.sector_code] = float(ev.factors.get("concentration", 0.0))
            eval_by_key[ev.sector_code] = float(ev.score)

        per_sym: dict[str, float] = {}
        for c in pool:
            keys: list[str] = []
            if c.sector_code:
                keys.append(f"F:{str(c.sector_code).strip()}")
            if c.dynamic_sector_code:
                keys.append(str(c.dynamic_sector_code))
            per_sym[c.symbol] = max((eval_by_key.get(k, 0.0) for k in keys), default=0.0)

        sec_score_map: dict[str, float] = {s.sector_code: float(s.score) for s in merged_scores}
        try:
            snapshots = self._intraday_loader.load_many(
                pool,
                sec_score_map,
                per_symbol_sector_score=per_sym,
            )
        except Exception as e:
            self._log.error("load_many snapshots aborted: %s", e)
            snapshots = []

        stock_scores = self._score.score_stocks(
            pool,
            snapshots,
            merged_scores,
            blend_config=cfg,
            breadth_by_key=breadth_by_key,
            eval_score_by_key=eval_by_key,
            concentration_by_key=conc_by_key,
        )
        adjusted = self._apply_filters_universe(pool, snapshots, stock_scores, per_sym, top_codes)
        self._snap_by_symbol = {s.symbol: s for s in snapshots}
        self._last_stock_scores = list(adjusted)

        leaders = self.pick_leaders_by_sector(adjusted)
        result = ScanResult(
            as_of=as_of,
            top_sectors=top_sectors,
            leaders_by_sector=leaders,
            all_sector_scores=merged_scores,
            all_stock_scores=adjusted,
            fixed_sector_results=list(fixed_results),
            dynamic_sector_results=list(dynamic_results),
        )
        self._save_result(result)
        return result

    def _leader_key(self, c: StockCandidate, top_codes: set[str]) -> str:
        if c.dynamic_sector_code and c.dynamic_sector_code in top_codes:
            return str(c.dynamic_sector_code)
        if c.sector_code and f"F:{c.sector_code}" in top_codes:
            return f"F:{c.sector_code}"
        if c.dynamic_sector_code:
            return str(c.dynamic_sector_code)
        if c.sector_code:
            return f"F:{c.sector_code}"
        return "UNKNOWN"

    def _apply_filters_universe(
        self,
        pool: list[StockCandidate],
        snapshots: list[StockSnapshot],
        stock_scores: list[StockScore],
        per_sym: dict[str, float],
        top_codes: set[str],
    ) -> list[StockScore]:
        snap_by = {s.symbol: s for s in snapshots}
        cand_by = {c.symbol: c for c in pool}
        out: list[StockScore] = []
        for ss in stock_scores:
            cand = cand_by.get(ss.symbol)
            snap = snap_by.get(ss.symbol)
            if cand is None or snap is None:
                out.append(
                    StockScore(
                        symbol=ss.symbol,
                        name=ss.name,
                        sector_code=ss.sector_code,
                        sector_name=ss.sector_name,
                        score=ss.score,
                        factors=dict(ss.factors),
                        passed_filters=False,
                        reject_reason="missing_candidate_or_snapshot",
                    )
                )
                continue
            sc = float(per_sym.get(cand.symbol, 0.0))
            ok, reason = passes_filters(cand, snap, self._cfg, sc)
            fac = dict(ss.factors)
            fac["leader_sector_key"] = self._leader_key(cand, top_codes)
            out.append(
                StockScore(
                    symbol=ss.symbol,
                    name=ss.name,
                    sector_code=ss.sector_code,
                    sector_name=ss.sector_name,
                    score=ss.score,
                    factors=fac,
                    passed_filters=ok,
                    reject_reason="" if ok else reason,
                )
            )
        return out

    def _apply_filters(
        self,
        pool: list[StockCandidate],
        snapshots: list[StockSnapshot],
        stock_scores: list[StockScore],
        sec_score_map: dict[str, float],
    ) -> list[StockScore]:
        snap_by = {s.symbol: s for s in snapshots}
        cand_by = {c.symbol: c for c in pool}
        out: list[StockScore] = []
        for ss in stock_scores:
            cand = cand_by.get(ss.symbol)
            snap = snap_by.get(ss.symbol)
            if cand is None or snap is None:
                out.append(
                    StockScore(
                        symbol=ss.symbol,
                        name=ss.name,
                        sector_code=ss.sector_code,
                        sector_name=ss.sector_name,
                        score=ss.score,
                        factors=dict(ss.factors),
                        passed_filters=False,
                        reject_reason="missing_candidate_or_snapshot",
                    )
                )
                continue
            sc = float(sec_score_map.get(cand.sector_code or "", 0.0))
            ok, reason = passes_filters(cand, snap, self._cfg, sc)
            out.append(
                StockScore(
                    symbol=ss.symbol,
                    name=ss.name,
                    sector_code=ss.sector_code,
                    sector_name=ss.sector_name,
                    score=ss.score,
                    factors=dict(ss.factors),
                    passed_filters=ok,
                    reject_reason="" if ok else reason,
                )
            )
        return out

    def _save_result(self, result: ScanResult) -> None:
        self._leader_sector_display = build_leader_sector_display_map(result)
        try:
            self._state.save_last_result(result)
        except Exception as e:
            self._log.warning("save_last_result failed: %s", e)

    def refresh_realtime(self, symbols: list[str], max_events: int | None = None) -> dict[str, Any]:
        now = pd.Timestamp.now(tz=self._cfg.timezone)
        prev = self._state.load_runtime_state()
        buf_raw = prev.get("tick_buffer")
        buf_in: dict[str, list[dict[str, Any]]] = {}
        if isinstance(buf_raw, dict):
            for k, v in buf_raw.items():
                if isinstance(v, list):
                    buf_in[str(k)] = [x for x in v if isinstance(x, dict)]

        events: list[dict[str, Any]] = []
        n = 0
        try:
            for ev in self._ws.stream(symbols):
                if isinstance(ev, dict):
                    events.append(ev)
                n += 1
                if max_events is not None and n >= max_events:
                    break
        except Exception as e:
            self._log.warning("refresh_realtime stream error: %s", e)

        win = float(self._cfg.realtime_tick_window_minutes)
        buf = merge_tick_buffer(buf_in, events, timezone=self._cfg.timezone, window_minutes=win, now=now)

        if not self._last_stock_scores:
            payload: dict[str, Any] = {
                "tick_buffer": buf,
                "events_captured": len(events),
                "realtime_as_of": now.isoformat(),
                "leader_sector_display": dict(self._leader_sector_display),
                "note": "no_baseline_scan_scores",
            }
            try:
                self._state.save_runtime_state(payload)
            except Exception as e:
                self._log.warning("save_runtime_state failed: %s", e)
            self._log.info("refresh_realtime captured %d events (no baseline scores)", len(events))
            return payload

        adjusted = adjust_scores_with_ticks(
            self._last_stock_scores,
            self._snap_by_symbol,
            buf,
            timezone=self._cfg.timezone,
            window_minutes=win,
            weight_intensity=float(self._cfg.realtime_weight_intensity),
            bonus_new_high=float(self._cfg.realtime_bonus_new_high),
            penalty_below_vwap=float(self._cfg.realtime_penalty_below_vwap),
        )
        leaders_rt = self.pick_leaders_by_sector(adjusted)

        payload = {
            "tick_buffer": buf,
            "events_captured": len(events),
            "realtime_as_of": now.isoformat(),
            "leader_sector_display": dict(self._leader_sector_display),
            "stock_scores_realtime": [model_to_dict(s) for s in adjusted],
            "leaders_by_sector_realtime": {
                sec: [model_to_dict(x) for x in rows] for sec, rows in leaders_rt.items()
            },
        }
        try:
            self._state.save_runtime_state(payload)
        except Exception as e:
            self._log.warning("save_runtime_state failed: %s", e)

        self._log.info(
            "refresh_realtime: events=%d buffer_symbols=%d adjusted_scores=%d leader_groups=%d",
            len(events),
            len(buf),
            len(adjusted),
            len(leaders_rt),
        )
        return payload

    def run(self) -> ScanResult:
        res = self.scan_once()
        if self._cfg.websocket_enabled:
            syms: list[str] = []
            for group in res.leaders_by_sector.values():
                for s in group:
                    syms.append(s.symbol)
            syms = sorted(set(syms))[:50]
            if syms:
                self.refresh_realtime(syms, max_events=8)
        return res
