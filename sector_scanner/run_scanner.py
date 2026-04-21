"""주도 섹터/주도주 스캐너 엔트리 (mock 기본)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from .fixed_sector_map import attach_fixed_sector
from .intraday_loader import IntradayLoader
from .kis_client import KISClient, build_kis_client_for_mode
from .mock_client import MockClient
from .ranking_loader import RankingLoader
from .scanner_config import ScannerConfig
from .dynamic_sector_cluster import DynamicSectorCluster
from .scanner_engine import ScannerEngine
from .scanner_state import ScannerStateStore
from .score_chart import ScoreHistoryBuffer
from .score_engine import ScoreEngine
from .sector_loader import SectorLoader
from .sector_mapper import SectorMapper
from .universe_loader import UniverseLoader
from .universe_export import (
    apply_realtime_overlay_to_scores,
    build_universe_package,
    export_universe_json,
    leaders_from_runtime_state,
)
from .websocket_client import WebSocketClientAdapter


def ensure_utf8_console() -> None:
    """Windows: UTF-8 console and stdio for correct Korean in terminal output."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        _CP_UTF8 = 65001
        ctypes.windll.kernel32.SetConsoleOutputCP(_CP_UTF8)
        ctypes.windll.kernel32.SetConsoleCP(_CP_UTF8)
    except Exception:
        pass
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError, ValueError):
        try:
            import io

            if hasattr(sys.stdout, "buffer"):
                sys.stdout = io.TextIOWrapper(
                    sys.stdout.buffer,
                    encoding="utf-8",
                    errors="replace",
                    line_buffering=True,
                )
            if hasattr(sys.stderr, "buffer"):
                sys.stderr = io.TextIOWrapper(
                    sys.stderr.buffer,
                    encoding="utf-8",
                    errors="replace",
                    line_buffering=True,
                )
        except Exception:
            pass


def setup_logging(log_file: str) -> logging.Logger:
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.handlers.clear()
    root.addHandler(fh)
    root.addHandler(sh)
    return logging.getLogger("sector_scanner")


def build_config(mode: str | None = None, *, use_universe_pipeline: bool = True) -> ScannerConfig:
    """데모용 완화된 임계값 + 출력 경로."""
    base = Path(__file__).resolve().parent
    out = base / "output"
    m = (mode or "mock").strip().lower()
    return ScannerConfig(
        mode=m,
        use_universe_pipeline=bool(use_universe_pipeline),
        top_sector_n=5,
        top_stock_k_per_sector=3,
        candidate_pool_limit=150 if use_universe_pipeline else 80,
        universe_target_size=400 if use_universe_pipeline else 80,
        universe_min_value_traded=120_000_000.0,
        dynamic_n_clusters=24,
        sector_refresh_seconds=300,
        ranking_refresh_seconds=120,
        intraday_refresh_seconds=60,
        websocket_enabled=True,
        min_price=500.0,
        min_value_traded=100_000_000.0,
        min_volume_ratio=0.15,
        max_intraday_pullback_pct=0.12,
        vwap_required=False,
        use_program_flow=True,
        use_foreign_institution_flow=True,
        output_dir=str(out),
        state_file=str(out / "scanner_state.json"),
        log_file=str(out / "scanner.log"),
        timezone="Asia/Seoul",
        market_open_time="09:00",
        market_close_time="15:30",
        score_round_digits=4,
    )


def build_components(cfg: ScannerConfig, log: logging.Logger):
    if cfg.mode == "mock":
        if cfg.use_universe_pipeline:
            client: MockClient | KISClient = MockClient(
                seed=7,
                n_stocks=min(500, max(350, int(cfg.universe_target_size))),
                n_groups=min(30, max(8, int(cfg.dynamic_n_clusters))),
            )
        else:
            client = MockClient(seed=7, n_stocks=12, n_groups=3)
        client.authenticate()
    elif cfg.mode in ("paper", "real"):
        client = build_kis_client_for_mode(cfg.mode)
        client.authenticate()
    else:
        raise ValueError(f"Unknown mode {cfg.mode!r}")

    fixed_map = attach_fixed_sector(client)
    mapper = SectorMapper(fixed_map if fixed_map else None)
    sector_loader = SectorLoader(client, log)
    ranking_loader = RankingLoader(client, mapper, log)
    intraday_loader = IntradayLoader(client, log)
    ws = WebSocketClientAdapter(client, log)
    score_engine = ScoreEngine()
    state_store = ScannerStateStore(cfg.state_file)
    ul = UniverseLoader(client, log) if cfg.use_universe_pipeline else None
    clusterer = (
        DynamicSectorCluster(
            n_clusters=int(cfg.dynamic_n_clusters),
            min_cluster_size=int(cfg.dynamic_min_cluster_size),
            max_cluster_size=int(cfg.dynamic_max_cluster_size),
        )
        if cfg.use_universe_pipeline
        else None
    )
    engine = ScannerEngine(
        cfg,
        sector_loader,
        ranking_loader,
        intraday_loader,
        ws,
        score_engine,
        state_store,
        log,
        universe_loader=ul,
        clusterer=clusterer,
        data_client=client if cfg.use_universe_pipeline else None,
    )
    return engine, client


def pretty_print_result(result) -> None:
    from .models import ScanResult, build_leader_sector_display_map, format_leader_sector_heading

    assert isinstance(result, ScanResult)
    leader_disp = build_leader_sector_display_map(result)
    print("\n" + "=" * 72)
    print(f"스캔 시각: {result.as_of}")
    print("=" * 72)
    print(f"\n[고정 섹터 평가] n={len(result.fixed_sector_results)}  [동적 섹터 평가] n={len(result.dynamic_sector_results)}")
    print("\n[Top sectors]")
    for s in result.top_sectors:
        code = str(s.sector_code).strip()
        nm = str(s.sector_name or "").strip()
        if nm and code and code not in nm:
            sec_line = f"{nm} ({code})"
        elif nm:
            sec_line = nm
        else:
            sec_line = code
        print(f"  {sec_line}: score={s.score:.2f}  factors={s.factors}")
    print("\n[Leaders by sector]")
    for sec, leaders in sorted(result.leaders_by_sector.items()):
        heading = format_leader_sector_heading(sec, leader_disp)
        print(f"  --- {heading} ---")
        for st in leaders:
            flag = "OK" if st.passed_filters else "X "
            nm = (st.name or st.symbol).strip()
            print(
                f"    [{flag}] {st.symbol} | {nm} | score={st.score:.2f}  {st.reject_reason or ''}"
            )
    print("\n[Stock scores sample: passed only, top 12 by score]")
    passed = [x for x in result.all_stock_scores if x.passed_filters]
    passed.sort(key=lambda x: x.score, reverse=True)
    for st in passed[:12]:
        nm = (st.name or st.symbol).strip()
        print(f"  {st.symbol} | {nm} | score={st.score:.2f}")
    print("=" * 72 + "\n")


def _leader_symbols_from_result(result) -> list[str]:
    from .models import ScanResult

    assert isinstance(result, ScanResult)
    syms: list[str] = []
    for group in result.leaders_by_sector.values():
        for s in group:
            syms.append(s.symbol)
    return sorted(set(syms))


def _load_runtime_state(state_file: str) -> dict:
    rt_path = Path(state_file).with_name(Path(state_file).stem + "_runtime.json")
    if not rt_path.is_file():
        return {}
    try:
        data = json.loads(rt_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _flush_score_chart(
    chart: ScoreHistoryBuffer | None,
    chart_path: Path,
    *,
    result=None,
    record_scan: bool,
    record_runtime: bool,
    state_file: str,
    log: logging.Logger,
    log_updates: bool = True,
) -> None:
    if chart is None:
        return
    if record_scan and result is not None:
        chart.record_scan_result(result)
    if record_runtime:
        rt = _load_runtime_state(state_file)
        if rt:
            chart.record_runtime_scores(rt)
    ok = chart.write_plotly_html(chart_path, title="스캐너 점수 변화 (full_scan · realtime)")
    if ok:
        if log_updates:
            log.info("Score chart updated: %s", chart_path)
        else:
            log.debug("Score chart updated: %s", chart_path)
    else:
        log.warning("Score chart not written (install plotly: pip install plotly)")


def run_continuous_loop(
    engine: ScannerEngine,
    cfg: ScannerConfig,
    log: logging.Logger,
    *,
    full_scan_interval: float,
    tick_interval: float,
    tick_max_events: int,
    quiet: bool,
    export_universe: bool,
    universe_out: Path | None,
    score_chart: ScoreHistoryBuffer | None,
    score_chart_path: Path,
) -> None:
    """
    장중 실시간 루프: ``full_scan_interval`` 마다 ``scan_once``, 매 주기마다 리더 심볼에 대해 ``refresh_realtime``.

    종료: Ctrl+C (KeyboardInterrupt).
    """
    last_full = 0.0
    result = engine.scan_once()
    last_full = time.monotonic()
    if not quiet:
        pretty_print_result(result)
    else:
        log.info("Initial scan as_of=%s leaders=%d", result.as_of, len(_leader_symbols_from_result(result)))

    if export_universe:
        _write_universe_export(cfg, result, runtime_after_ticks=False, log=log, universe_out=universe_out)

    _flush_score_chart(
        score_chart,
        score_chart_path,
        result=result,
        record_scan=True,
        record_runtime=False,
        state_file=cfg.state_file,
        log=log,
    )
    if not quiet and score_chart is not None:
        print(f"점수 차트 HTML: {score_chart_path}\n")

    try:
        while True:
            now = time.monotonic()
            if now - last_full >= full_scan_interval:
                result = engine.scan_once()
                last_full = now
                if not quiet:
                    pretty_print_result(result)
                else:
                    log.info("Full scan as_of=%s", result.as_of)
                if export_universe:
                    _write_universe_export(cfg, result, runtime_after_ticks=False, log=log, universe_out=universe_out)
                _flush_score_chart(
                    score_chart,
                    score_chart_path,
                    result=result,
                    record_scan=True,
                    record_runtime=False,
                    state_file=cfg.state_file,
                    log=log,
                )
                if not quiet and score_chart is not None:
                    print(f"점수 차트 HTML: {score_chart_path}\n")

            syms = _leader_symbols_from_result(result)
            if cfg.websocket_enabled and syms:
                engine.refresh_realtime(syms[:50], max_events=tick_max_events)
                if not quiet:
                    _print_realtime_leaders(cfg.state_file)
                _flush_score_chart(
                    score_chart,
                    score_chart_path,
                    result=None,
                    record_scan=False,
                    record_runtime=True,
                    state_file=cfg.state_file,
                    log=log,
                    log_updates=False,
                )

            time.sleep(max(0.1, tick_interval))

    except KeyboardInterrupt:
        log.info("Continuous loop stopped by user (KeyboardInterrupt).")


def _write_universe_export(
    cfg: ScannerConfig,
    result,
    *,
    runtime_after_ticks: bool,
    log: logging.Logger,
    universe_out: Path | None,
) -> None:
    rt_path = Path(cfg.state_file).with_name(Path(cfg.state_file).stem + "_runtime.json")
    runtime: dict = {}
    if runtime_after_ticks and rt_path.is_file():
        try:
            runtime = json.loads(rt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.warning("Universe export: could not read runtime: %s", e)
    leaders_ov = leaders_from_runtime_state(runtime)
    scores_merged = apply_realtime_overlay_to_scores(result.all_stock_scores, runtime)
    pkg = build_universe_package(
        result,
        stock_scores_override=scores_merged,
        leaders_override=leaders_ov,
        only_passed=True,
        prefer_leaders_only=False,
    )
    out_p = universe_out or (Path(cfg.output_dir) / "scanner_universe.json")
    export_universe_json(pkg, out_p)
    log.info("Universe export: %s (%d symbols)", out_p, len(pkg.symbol_list))


def _load_leader_sector_display_fallback(state_file: str) -> dict[str, str]:
    """런타임 JSON에 맵이 없을 때 scanner_state.json에서 복원."""
    p = Path(state_file)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    m: dict[str, str] = {}

    def _ingest_eval_list(items: object) -> None:
        if not isinstance(items, list):
            return
        for ev in items:
            if not isinstance(ev, dict):
                continue
            k = str(ev.get("sector_code", "")).strip()
            nm = str(ev.get("sector_name", "")).strip()
            if k and nm:
                m[k] = nm

    _ingest_eval_list(data.get("fixed_sector_results"))
    _ingest_eval_list(data.get("dynamic_sector_results"))
    for key in ("all_sector_scores", "top_sectors"):
        rows = data.get(key)
        if not isinstance(rows, list):
            continue
        for s in rows:
            if not isinstance(s, dict):
                continue
            k = str(s.get("sector_code", "")).strip()
            nm = str(s.get("sector_name", "")).strip()
            if k and nm:
                m.setdefault(k, nm)
    return m


def _print_realtime_leaders(state_file: str) -> None:
    rt_path = Path(state_file).with_name(Path(state_file).stem + "_runtime.json")
    if not rt_path.is_file():
        return
    try:
        rt = json.loads(rt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    leaders = rt.get("leaders_by_sector_realtime")
    if not isinstance(leaders, dict) or not leaders:
        return
    from .models import format_leader_sector_heading

    disp_raw = rt.get("leader_sector_display")
    disp: dict[str, str] = disp_raw if isinstance(disp_raw, dict) else {}
    if not disp:
        disp = _load_leader_sector_display_fallback(state_file)
    print("\n[Leaders by sector — realtime tick overlay]")
    for sec in sorted(leaders.keys()):
        heading = format_leader_sector_heading(str(sec), disp)
        print(f"  --- {heading} ---")
        for row in leaders[sec]:
            if not isinstance(row, dict):
                continue
            sym = row.get("symbol", "")
            nm = str(row.get("name", sym) or sym).strip()
            sc = row.get("score", 0)
            fac = row.get("factors") or {}
            rd = fac.get("realtime_delta", "")
            base = fac.get("base_score_before_realtime", "")
            print(f"    {sym} | {nm} | score={sc} | Δ실시간={rd} | 기준점수={base}")
    print("")


def main(argv: list[str] | None = None) -> int:
    ensure_utf8_console()

    p = argparse.ArgumentParser(description="KIS-style leader sector/stock scanner (mock by default).")
    p.add_argument("--mode", choices=("mock", "paper", "real"), default=None, help="Override ScannerConfig.mode")
    p.add_argument(
        "--no-universe-pipeline",
        action="store_true",
        help="대형 유니버스·동적 섹터 파이프라인 끄고 기존 소형 레거시 경로만 사용.",
    )
    once_grp = p.add_mutually_exclusive_group()
    once_grp.add_argument("--once", action="store_true", help="scan_once only (no optional websocket batch in run())")
    once_grp.add_argument(
        "--loop",
        action="store_true",
        help="실시간 루프: 주기적 전체 스캔 + 리더 심볼 틱 갱신 (Ctrl+C 종료).",
    )
    p.add_argument(
        "--loop-scan-seconds",
        type=float,
        default=None,
        help="루프 모드에서 scan_once 주기(초). 기본: ScannerConfig.ranking_refresh_seconds",
    )
    p.add_argument(
        "--loop-tick-seconds",
        type=float,
        default=None,
        help="루프 모드에서 틱 갱신 사이 대기(초). 기본: ScannerConfig.intraday_refresh_seconds",
    )
    p.add_argument(
        "--loop-tick-events",
        type=int,
        default=20,
        help="루프 모드에서 refresh_realtime 당 최대 체결 이벤트 수.",
    )
    p.add_argument(
        "--loop-quiet",
        action="store_true",
        help="루프 모드에서 전체 표 출력 생략(로그만).",
    )
    p.add_argument(
        "--export-universe",
        action="store_true",
        help="Write scanner universe JSON for backtest/live (§20-2).",
    )
    p.add_argument(
        "--universe-out",
        type=Path,
        default=None,
        help="Output path for universe JSON (default: output/scanner_universe.json).",
    )
    p.add_argument(
        "--no-score-chart",
        action="store_true",
        help="점수 시계열 HTML 차트를 쓰지 않음 (기본: output/score_history.html 갱신).",
    )
    p.add_argument(
        "--score-chart-out",
        type=Path,
        default=None,
        help="Plotly HTML 차트 경로 (기본: output/score_history.html).",
    )
    args = p.parse_args(argv)

    cfg = build_config(args.mode, use_universe_pipeline=not bool(args.no_universe_pipeline))
    Path(cfg.output_dir).mkdir(parents=True, exist_ok=True)
    log = setup_logging(cfg.log_file)
    log.info("Starting scanner mode=%s", cfg.mode)

    score_chart: ScoreHistoryBuffer | None = None if args.no_score_chart else ScoreHistoryBuffer()
    score_chart_path = args.score_chart_out or (Path(cfg.output_dir) / "score_history.html")

    try:
        engine, _client = build_components(cfg, log)
    except Exception as e:
        log.exception("build_components failed: %s", e)
        return 1

    try:
        if args.loop:
            if not cfg.websocket_enabled:
                log.warning("--loop 권장: websocket_enabled=True (현재 False면 틱 갱신이 스킵됩니다).")
            full_iv = float(args.loop_scan_seconds) if args.loop_scan_seconds is not None else float(cfg.ranking_refresh_seconds)
            tick_iv = float(args.loop_tick_seconds) if args.loop_tick_seconds is not None else float(cfg.intraday_refresh_seconds)
            log.info(
                "Continuous loop: full_scan every %.1fs, tick refresh every %.1fs (max_events=%d)",
                full_iv,
                tick_iv,
                int(args.loop_tick_events),
            )
            run_continuous_loop(
                engine,
                cfg,
                log,
                full_scan_interval=full_iv,
                tick_interval=tick_iv,
                tick_max_events=max(1, int(args.loop_tick_events)),
                quiet=bool(args.loop_quiet),
                export_universe=bool(args.export_universe),
                universe_out=args.universe_out,
                score_chart=score_chart,
                score_chart_path=score_chart_path,
            )
            log.info("Done (loop). state_file=%s", cfg.state_file)
            return 0

        if args.once:
            result = engine.scan_once()
        else:
            result = engine.run()
    except Exception as e:
        log.exception("scan failed: %s", e)
        return 2

    pretty_print_result(result)
    if not args.once:
        _print_realtime_leaders(cfg.state_file)

    if args.export_universe:
        rt_path = Path(cfg.state_file).with_name(Path(cfg.state_file).stem + "_runtime.json")
        runtime: dict = {}
        if not args.once and rt_path.is_file():
            try:
                runtime = json.loads(rt_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                log.warning("Could not read runtime state for export: %s", e)
        leaders_ov = leaders_from_runtime_state(runtime)
        scores_merged = apply_realtime_overlay_to_scores(result.all_stock_scores, runtime)
        pkg = build_universe_package(
            result,
            stock_scores_override=scores_merged,
            leaders_override=leaders_ov,
            only_passed=True,
            prefer_leaders_only=False,
        )
        out_p = args.universe_out or (Path(cfg.output_dir) / "scanner_universe.json")
        export_universe_json(pkg, out_p)
        log.info("Universe export written: %s (%d symbols)", out_p, len(pkg.symbol_list))
        print(f"Universe export: {out_p} ({len(pkg.symbol_list)} symbols)")

    if score_chart is not None:
        _flush_score_chart(
            score_chart,
            score_chart_path,
            result=result,
            record_scan=True,
            record_runtime=not args.once,
            state_file=cfg.state_file,
            log=log,
        )
        print(f"점수 차트 HTML: {score_chart_path}")

    log.info("Done. state_file=%s", cfg.state_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
