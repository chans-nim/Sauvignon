"""
Thema 대분류/중분류 기반 섹터 리포트 생성기.

기존 KIS 업종 리포트(`scripts.collect_sector_data`)와 별개로 실행한다.

예시:
  python -m scripts.collect_thema_sector_data
  python -m scripts.collect_thema_sector_data --mode real --classification-json "C:/Users/me/Downloads/thema_major_middle_stock_classification_dup_allowed.json"
  python -m scripts.collect_thema_sector_data --no-quote-enrichment
  python -m scripts.collect_thema_sector_data --full-quote-enrichment
  python -m scripts.collect_thema_sector_data --mode real --telegram

기본은 **2단계**다. (1) 분류 **전 구성주**에 KIS **현재가(등락·거래대금)** 만 병렬 조회한 뒤, 라이브 랭킹·순위 수급·JSON 시총과 합쳐 **전 그룹을 탐색**해 섹터(대/중분류)마다 RS 상위 **top N**(기본 5)을 뽑고,
(2) 그 **선정 종목**에만 **프로그램**(및 순위에 없을 때 종목별 수급)까지 포함한 보강을 추가 호출한다(선정 종목 시세는 재조회될 수 있음).
`--full-quote-enrichment` 는 1차 프로브 생략·전 구성주에 2차와 동일 풀보강. 경량만 필요하면 `--no-quote-enrichment`.
병렬도는 `--enrichment-workers`(기본 4)로 조절한다. 순위 API로 외국인·기관이 이미 채워지면 종목당 2회(시세·프로그램)만 호출한다.

외국인·기관 순매수 거래대금은 **순위 API 한 번**으로 합산·표시(순위에 없거나 코드 불일치 시 `-`).

분류 JSON는 `~/Downloads/...` 대신 `data/lake/sector/thema_major_middle_stock_classification_dup_allowed.json` 에 두어도 기본 인자로 인식한다.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

if __name__ == "__main__" and str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.collect_sector_data import (
    _escape_html,
    _fmt_eok,
    _fmt_num,
    _fmt_pct,
    _fmt_ts,
    _naver_finance_stock_url,
    _pct_rank_map,
    _send_telegram_document,
    _send_telegram_message,
)


def _fmt_signed_eok(x: Any) -> str:
    """순매수 금액(원 등)을 억 단위 표기, 부호 유지."""
    if x is None:
        return "-"
    try:
        v = float(x) / 100_000_000.0
    except Exception:
        return "-"
    return f"{v:+,.1f}"
from sector_scanner.kis_client import build_kis_client_for_mode


_CLASSIFICATION_BASENAME = "thema_major_middle_stock_classification_dup_allowed.json"
DEFAULT_CLASSIFICATION_JSON = Path.home() / "Downloads" / _CLASSIFICATION_BASENAME
DEFAULT_OUTPUT_DIR = Path("data/lake/sector/thema_major_middle")
DEFAULT_THEME_HISTORY_DIR = Path("data/theme_history")

# `relative_strength_score` = 주도·실시간 기준(등락·거래대금) 비중이 크도록, RS(합성)는 보조. 합 1.0, 코호트=동일 대/중 그룹끼리
GROUP_SCORE_W_REP_RETURN_COHORT = 0.26  # 대표(top_n) 평균 등락 백분위
GROUP_SCORE_W_REP_VALUE_SUM_COHORT = 0.24  # 대표(top_n) 거래대금 합 백분위(시세 있을 때)
GROUP_SCORE_W_WEIGHTED_RS = 0.18  # 시총가중 합성 RS(보조)
GROUP_SCORE_W_AVG_RS = 0.08
GROUP_SCORE_W_HIGH_RS_SHARE = 0.08
GROUP_SCORE_W_CAP_RANK = 0.04
GROUP_SCORE_W_COUNT_RANK = 0.04
GROUP_SCORE_W_LIVE_COVER_COHORT = 0.08  # 구성주 라이브 랭킹 커버 백분위(실시간 붙는 비중)

# 종목 합성 `rs`(그룹 내 상대): 주도=등락+거래대금 우선, 랭킹·강도·고가는 보조. 합 1.0
STOCK_RS_W_LIVE = 0.18
STOCK_RS_W_RETURN = 0.32
STOCK_RS_W_VALUE = 0.35
STOCK_RS_W_TRADE_STRENGTH = 0.10
STOCK_RS_W_NEAR_HIGH = 0.05


def _repo_classification_json() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "lake" / "sector" / _CLASSIFICATION_BASENAME


def _resolve_classification_json(requested: Path) -> Path:
    """Use repo `data/lake/sector/<basename>` when the default Downloads path is missing."""
    if requested.is_file():
        return requested
    try:
        is_default = requested.resolve() == DEFAULT_CLASSIFICATION_JSON.resolve()
    except OSError:
        is_default = False
    if is_default:
        alt = _repo_classification_json()
        if alt.is_file():
            return alt
    return requested


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(str(x).replace(",", ""))
    except Exception:
        return default


def _clip(x: float, lo: float, hi: float) -> float:
    return max(float(lo), min(float(hi), float(x)))


def _round1(x: Any) -> float | None:
    if x is None:
        return None
    try:
        return round(float(x), 1)
    except Exception:
        return None


def _parse_iso_date(s: str) -> str | None:
    """
    collected_at(예: 2026-05-06T21:10:00+09:00)에서 YYYY-MM-DD를 뽑는다.
    """
    st = str(s or "").strip()
    if len(st) >= 10 and st[4] == "-" and st[7] == "-":
        return st[:10]
    return None


def _date_yyyymmdd(d: str) -> str:
    return str(d).replace("-", "")


def classify_theme_quality(persistence_score: float | None, breadth_score: float | None) -> str:
    if persistence_score is None or breadth_score is None:
        return "데이터부족"
    p = float(persistence_score)
    b = float(breadth_score)
    if p >= 70 and b >= 70:
        return "주도테마"
    if p >= 70 and b < 50:
        return "대장주 편중"
    if p < 50 and b >= 70:
        return "단기 순환매"
    if p >= 50 and b >= 50:
        return "관심테마"
    return "약세/제외"


def _calc_simple_slope(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    return (float(values[-1]) - float(values[0])) / float(len(values) - 1)


def calculate_persistence_score(
    current_group: dict[str, Any],
    history_rows: list[dict[str, Any]],
    group_key: tuple[str, str | None],
) -> dict[str, Any]:
    """
    특정 대분류/중분류의 최근 5일, 10일, 20일 데이터를 기반으로 persistence_score 및 세부 지표를 계산한다.

    history_rows: 동일 group_type 코호트의 "theme snapshot row" 들(여러 날짜 섞임).
    group_key: (major_category, middle_category)
    """
    major, middle = group_key

    def _match(r: dict[str, Any]) -> bool:
        return str(r.get("major_category") or "") == str(major or "") and (r.get("middle_category") == middle)

    rows = [r for r in (history_rows or []) if isinstance(r, dict) and _match(r)]
    # date desc (string YYYY-MM-DD)
    rows.sort(key=lambda r: str(r.get("date") or ""), reverse=True)

    # prepend current row as "today"
    cur = dict(current_group or {})
    cur_date = str(cur.get("date") or "").strip()
    if cur_date:
        rows = [cur] + [r for r in rows if str(r.get("date") or "") != cur_date]
    else:
        rows = [cur] + rows

    def _take(n: int) -> list[dict[str, Any]]:
        return rows[: max(0, int(n))]

    last5 = _take(5)
    last10 = _take(10)
    last20 = _take(20)

    if len(last5) < 5:
        return {
            "persistence_score": None,
            "rank_top3_days_5d": 0,
            "rank_top5_days_10d": 0,
            "rs_avg_5d": None,
            "rs_avg_10d": None,
            "rs_slope_5d": None,
            "value_ratio_5d_20d": None,
        }

    rs5 = [float(r.get("theme_rs") or 0.0) for r in last5]
    rs10 = [float(r.get("theme_rs") or 0.0) for r in last10] if last10 else []

    rs_avg_5d = sum(rs5) / len(rs5) if rs5 else 0.0
    rs_avg_10d = (sum(rs10) / len(rs10)) if rs10 else None
    rs_slope_5d = _calc_simple_slope(rs5)

    top3 = sum(1 for r in last5 if 1 <= int(r.get("theme_rank") or 10_000) <= 3)
    top5_10d = sum(1 for r in last10 if 1 <= int(r.get("theme_rank") or 10_000) <= 5)

    val5 = [float(r.get("total_value_traded") or 0.0) for r in last5]
    val20 = [float(r.get("total_value_traded") or 0.0) for r in last20]
    avg5 = (sum(val5) / len(val5)) if val5 else 0.0
    avg20 = (sum(val20) / len(val20)) if val20 else 0.0

    if avg20 <= 0:
        value_ratio_5d_20d = None
        value_persistence_score = 50.0
    else:
        value_ratio_5d_20d = avg5 / avg20
        if value_ratio_5d_20d >= 2.0:
            value_persistence_score = 100.0
        elif value_ratio_5d_20d >= 1.5:
            value_persistence_score = 80.0
        elif value_ratio_5d_20d >= 1.2:
            value_persistence_score = 60.0
        elif value_ratio_5d_20d >= 1.0:
            value_persistence_score = 40.0
        else:
            value_persistence_score = 20.0

    rs_avg_5d_score = _clip(rs_avg_5d, 0.0, 100.0)
    rank_persistence_score = float(top3) / 5.0 * 100.0

    persistence = 0.40 * rs_avg_5d_score + 0.30 * rank_persistence_score + 0.30 * float(value_persistence_score)
    return {
        "persistence_score": round(float(persistence), 1),
        "rank_top3_days_5d": int(top3),
        "rank_top5_days_10d": int(top5_10d),
        "rs_avg_5d": round(float(rs_avg_5d), 1),
        "rs_avg_10d": round(float(rs_avg_10d), 1) if rs_avg_10d is not None else None,
        "rs_slope_5d": round(float(rs_slope_5d), 2) if rs_slope_5d is not None else None,
        "value_ratio_5d_20d": round(float(value_ratio_5d_20d), 2) if value_ratio_5d_20d is not None else None,
    }


def calculate_breadth_score(
    *,
    member_count: int,
    rs60_ratio: float | None,
    rs70_ratio: float | None,
    up_ratio: float | None,
    market_up_ratio: float | None,
    value_expansion_ratio: float | None,
) -> dict[str, Any]:
    if member_count <= 0:
        return {
            "breadth_score": None,
            "rs60_ratio": 0.0,
            "rs70_ratio": 0.0,
            "up_ratio": 0.0,
            "market_up_ratio": market_up_ratio,
            "relative_up_ratio": None,
            "value_expansion_ratio": 0.0,
        }

    r60 = float(rs60_ratio or 0.0)
    r70 = float(rs70_ratio or 0.0)
    upr = float(up_ratio or 0.0)
    mkt = float(market_up_ratio or 0.0) if market_up_ratio is not None else None
    rel = (upr - float(mkt)) if mkt is not None else None
    vex = float(value_expansion_ratio or 0.0) if value_expansion_ratio is not None else None

    rs60_score = min(r60 / 0.30, 1.0) * 100.0
    relative_up_score = _clip(((float(rel or 0.0) + 0.20) / 0.40) * 100.0, 0.0, 100.0) if rel is not None else 50.0
    value_expansion_score = (min(float(vex) / 0.25, 1.0) * 100.0) if vex is not None else 50.0

    breadth = 0.40 * float(rs60_score) + 0.30 * float(relative_up_score) + 0.30 * float(value_expansion_score)
    return {
        "breadth_score": round(float(breadth), 1),
        "rs60_ratio": round(float(r60), 3),
        "rs70_ratio": round(float(r70), 3),
        "up_ratio": round(float(upr), 3),
        "market_up_ratio": round(float(mkt), 3) if mkt is not None else None,
        "relative_up_ratio": round(float(rel), 3) if rel is not None else None,
        "value_expansion_ratio": round(float(vex), 3) if vex is not None else None,
    }


def _theme_history_snapshot_path(history_dir: Path, date_yyyy_mm_dd: str) -> Path:
    return Path(history_dir) / f"theme_snapshot_{_date_yyyymmdd(date_yyyy_mm_dd)}.json"


def _load_theme_history_rows(history_dir: Path, *, max_days: int = 30) -> list[dict[str, Any]]:
    """
    data/theme_history/theme_snapshot_YYYYMMDD.json 을 여러 개 읽어 단일 row list로 평탄화.
    """
    hd = Path(history_dir)
    if not hd.exists():
        return []
    files = sorted(hd.glob("theme_snapshot_*.json"), key=lambda p: p.name, reverse=True)
    rows: list[dict[str, Any]] = []
    for p in files[: max(0, int(max_days))]:
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
            for r in payload["rows"]:
                if isinstance(r, dict):
                    rows.append(r)
        elif isinstance(payload, list):
            for r in payload:
                if isinstance(r, dict):
                    rows.append(r)
    return rows


def _write_theme_history_snapshot(history_dir: Path, *, date_yyyy_mm_dd: str, rows: list[dict[str, Any]]) -> Path:
    hd = Path(history_dir)
    hd.mkdir(parents=True, exist_ok=True)
    path = _theme_history_snapshot_path(hd, date_yyyy_mm_dd)
    payload = {
        "date": date_yyyy_mm_dd,
        "rows": rows,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _silver_parquet_paths() -> list[str]:
    # reuse project default: data/lake/silver/ohlcv_daily/**/data.parquet
    root = Path(__file__).resolve().parent.parent
    silver_dir = root / "data" / "lake" / "silver" / "ohlcv_daily"
    if not silver_dir.exists():
        return []
    return [p.as_posix() for p in silver_dir.rglob("data.parquet")]


def _load_symbol_value_averages(symbols: list[str], *, lookback_days: int = 30) -> dict[str, dict[str, float]]:
    """
    Silver OHLCV(일봉)에서 종목별 value(거래대금) 최근 N일을 읽어, avg5/avg20 을 계산한다.
    반환: {symbol: {"avg5": ..., "avg20": ...}}
    """
    syms = sorted({_norm_kis_stock_symbol(s) for s in (symbols or []) if _norm_kis_stock_symbol(s)})
    if not syms:
        return {}
    paths = _silver_parquet_paths()
    if not paths:
        return {}
    try:
        import duckdb  # type: ignore
    except Exception:
        return {}
    con = duckdb.connect()
    try:
        # 최근 lookback_days를 "행 기준"으로 맞추기 위해, symbol별 최근 N개를 window로 자른다.
        df = con.execute(
            """
            WITH base AS (
              SELECT
                symbol,
                CAST(date AS DATE) AS d,
                CAST(value AS DOUBLE) AS value
              FROM read_parquet(?)
              WHERE symbol IN (SELECT * FROM UNNEST(?))
            ),
            ranked AS (
              SELECT
                symbol,
                d,
                value,
                ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY d DESC) AS rn
              FROM base
              WHERE value IS NOT NULL
            )
            SELECT symbol, d, value, rn
            FROM ranked
            WHERE rn <= ?
            """,
            [paths, syms, int(lookback_days)],
        ).fetchdf()
    finally:
        con.close()
    if df is None or getattr(df, "empty", True):
        return {}
    out: dict[str, dict[str, float]] = {}
    for sym in syms:
        sub = df[df["symbol"] == sym].sort_values("d", ascending=False)
        vals = [float(x) for x in list(sub["value"].values) if float(x) > 0.0]
        if not vals:
            continue
        avg5 = sum(vals[:5]) / min(5, len(vals))
        avg20 = sum(vals[:20]) / min(20, len(vals))
        out[sym] = {"avg5": float(avg5), "avg20": float(avg20)}
    return out


def _norm_kis_stock_symbol(sym: str | None) -> str:
    """종목 코드를 KIS/JSON 간 공통 형식(숫자 6자리)으로 맞춘다."""
    s = str(sym or "").strip()
    return s.zfill(6) if s.isdigit() else s


def _build_investor_by_symbol(flow_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """KIS 외국인·기관 종합 순위 한 번 조회 결과 → 심별 집합."""
    out: dict[str, dict[str, Any]] = {}
    for row in flow_rows or []:
        if not isinstance(row, dict):
            continue
        sym = _norm_kis_stock_symbol(row.get("symbol", ""))
        if not sym:
            continue
        out[sym] = {
            "foreign_net_tr_pbmn": row.get("foreign_net_tr_pbmn"),
            "institution_net_tr_pbmn": row.get("institution_net_tr_pbmn"),
        }
    return out


def _fetch_quote_enrichment_concurrent(
    client: Any,
    symbols: list[str],
    *,
    fetch_investor_per_symbol: bool,
    max_workers: int = 4,
    fetch_program: bool = True,
    log_label: str = "[enrichment]",
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """
    종목별 시세·(옵션)외국인·기관·(옵션)프로그램을 한 번에 조회한다.
    스레드 풀 + KISClient 의 요청-시작 간격 스로틀(HTTP는 겹침)로 순차 3패스 대비 wall time 을 줄인다.
    fetch_program=False 이면 프로그램 TR 만 생략(1차 최소 시세용).
    """
    quotes_by_symbol: dict[str, dict[str, Any]] = {}
    investor_out: dict[str, dict[str, Any]] = {}
    program_by_symbol: dict[str, dict[str, Any]] = {}

    total = len(symbols)
    if total <= 0:
        return quotes_by_symbol, investor_out, program_by_symbol

    workers = max(1, min(int(max_workers), 32, total))
    lock = threading.Lock()
    done_holder: list[int] = [0]
    t0 = time.perf_counter()

    def _one(sym_raw: str) -> None:
        sym = _norm_kis_stock_symbol(sym_raw)
        quote: dict[str, Any] = {}
        try:
            q = client.fetch_stock_price(sym)
            if isinstance(q, dict) and q:
                quote = q
        except Exception:
            pass

        inv: dict[str, Any] = {}
        if fetch_investor_per_symbol:
            try:
                if hasattr(client, "fetch_foreign_institution_for_symbol"):
                    d = client.fetch_foreign_institution_for_symbol(sym)
                    if isinstance(d, dict):
                        inv = d
            except Exception:
                pass

        prog_val: dict[str, Any] = {}
        if fetch_program:
            try:
                if hasattr(client, "fetch_program_trade_net_for_symbol"):
                    d = client.fetch_program_trade_net_for_symbol(sym)
                    if isinstance(d, dict) and d.get("program_net_tr_pbmn") is not None:
                        prog_val = {"program_net_tr_pbmn": d.get("program_net_tr_pbmn")}
            except Exception:
                pass

        with lock:
            if quote:
                quotes_by_symbol[sym] = quote
            if fetch_investor_per_symbol and (
                inv.get("foreign_net_tr_pbmn") is not None or inv.get("institution_net_tr_pbmn") is not None
            ):
                investor_out[sym] = {
                    "foreign_net_tr_pbmn": inv.get("foreign_net_tr_pbmn"),
                    "institution_net_tr_pbmn": inv.get("institution_net_tr_pbmn"),
                }
            if prog_val:
                program_by_symbol[sym] = prog_val
            done_holder[0] += 1
            n = done_holder[0]
        if n % 25 == 0 or n == total:
            elapsed = time.perf_counter() - t0
            extra = ""
            if 0 < n < total and elapsed > 0.05:
                eta = (elapsed / n) * (total - n)
                extra = f"  eta~{eta:.0f}s"
            print(f"{log_label} {n}/{total}  {elapsed:.1f}s{extra}")

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_one, s) for s in symbols]
        for fut in as_completed(futures):
            fut.result()

    print(f"{log_label} done  {total} symbols  {time.perf_counter() - t0:.1f}s  (workers={workers})")
    return quotes_by_symbol, investor_out, program_by_symbol


def _load_classification_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"Invalid classification json: {path}")
    return data


def _normalize_stock(raw: dict[str, Any], *, major: str, middle: str) -> dict[str, Any]:
    per_values = []
    for key in ("per1", "per2", "per3", "per4", "per5"):
        val = raw.get(key)
        if val not in (None, ""):
            per_values.append(_safe_float(val))
    return {
        "symbol": _norm_kis_stock_symbol(raw.get("stockCode", "")),
        "name": str(raw.get("stockName", "")).strip(),
        "market_cap_eok": _safe_float(raw.get("marketCap"), 0.0),
        "per_values": per_values,
        "major_category": major,
        "middle_category": middle,
    }


def _dedupe_stocks(stocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for stock in stocks:
        sym = _norm_kis_stock_symbol(stock.get("symbol", ""))
        if not sym:
            continue
        prev = out.get(sym)
        if prev is None:
            out[sym] = dict(stock)
            continue
        if float(stock.get("market_cap_eok", 0.0)) > float(prev.get("market_cap_eok", 0.0)):
            out[sym] = dict(stock)
    return list(out.values())


def _sort_thema_members(stocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        stocks,
        key=lambda x: (
            -float(x.get("market_cap_eok", 0.0)),
            str(x.get("name", "")),
            str(x.get("symbol", "")),
        ),
    )


def _build_group_blueprints(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    major_rows = []
    middle_rows = []
    for major_item in data.get("major_categories") or []:
        if not isinstance(major_item, dict):
            continue
        major = str(major_item.get("majorCategory", "")).strip()
        if not major:
            continue
        sub_categories = major_item.get("subCategories") or []
        major_stocks_raw: list[dict[str, Any]] = []
        valid_sub_count = 0
        for sub in sub_categories:
            if not isinstance(sub, dict):
                continue
            middle = str(sub.get("middleCategory", "")).strip()
            if not middle:
                continue
            valid_sub_count += 1
            stocks = [
                _normalize_stock(s, major=major, middle=middle)
                for s in (sub.get("stocks") or [])
                if isinstance(s, dict)
            ]
            stocks = _dedupe_stocks(stocks)
            middle_rows.append(
                {
                    "group_type": "middle",
                    "major_category": major,
                    "middle_category": middle,
                    "display_name": middle,
                    "display_path": f"{major} > {middle}",
                    "member_count": len(stocks),
                    "sub_category_count": None,
                    "stocks": _sort_thema_members(stocks),
                }
            )
            major_stocks_raw.extend(stocks)
        major_rows.append(
            {
                "group_type": "major",
                "major_category": major,
                "middle_category": None,
                "display_name": major,
                "display_path": major,
                "member_count": len(_dedupe_stocks(major_stocks_raw)),
                "sub_category_count": valid_sub_count,
                "stocks": _sort_thema_members(_dedupe_stocks(major_stocks_raw)),
            }
        )
    return major_rows, middle_rows


def _position_score_map(rows: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    total = len(rows)
    if total <= 0:
        return out
    if total == 1:
        sym = _norm_kis_stock_symbol(rows[0].get("symbol", ""))
        if sym:
            out[sym] = 100.0
        return out
    for idx, row in enumerate(rows):
        sym = _norm_kis_stock_symbol(row.get("symbol", ""))
        if not sym:
            continue
        out[sym] = round(100.0 * (total - 1 - idx) / (total - 1), 2)
    return out


def _build_live_signal_map(client) -> dict[str, dict[str, Any]]:
    sources: list[tuple[str, list[dict[str, Any]], float]] = [
        ("return", client.fetch_ranking_return(), 0.25),
        ("volume", client.fetch_ranking_volume(), 0.25),
        ("trade_strength", client.fetch_ranking_trade_strength(), 0.30),
        ("near_high", client.fetch_ranking_near_high(), 0.20),
    ]
    signal_map: dict[str, dict[str, Any]] = {}
    for source_name, rows, weight in sources:
        if not isinstance(rows, list):
            continue
        score_map = _position_score_map(rows)
        for row in rows:
            sym = _norm_kis_stock_symbol(row.get("symbol", ""))
            if not sym:
                continue
            entry = signal_map.setdefault(
                sym,
                {
                    "live_signal_score": 0.0,
                    "source_scores": {},
                    "source_count": 0,
                },
            )
            raw_score = score_map.get(sym, 0.0)
            if raw_score <= 0.0:
                continue
            entry["live_signal_score"] += raw_score * weight
            entry["source_scores"][source_name] = raw_score
            entry["source_count"] = len(entry["source_scores"])
    for entry in signal_map.values():
        entry["live_signal_score"] = round(float(entry.get("live_signal_score", 0.0)), 2)
    return signal_map


def _all_unique_stock_symbols_from_groups(groups: list[dict[str, Any]]) -> list[str]:
    """대·중분류 블루프린트에 등장하는 구성주 심볼(중복 제거, 정렬)."""
    out: set[str] = set()
    for group in groups:
        for stock in group.get("stocks") or []:
            if not isinstance(stock, dict):
                continue
            sym = _norm_kis_stock_symbol(stock.get("symbol", ""))
            if sym:
                out.add(sym)
    return sorted(out)


def _probe_top_symbols_per_group(
    groups: list[dict[str, Any]],
    live_signal_map: dict[str, dict[str, Any]],
    *,
    top_n: int,
    investor_by_symbol: dict[str, dict[str, Any]] | None = None,
    program_by_symbol: dict[str, dict[str, Any]] | None = None,
    quotes_by_symbol: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    """
    그룹 내 RS를 매겨 각 그룹에서 상위 top_n 종목 코드를 모은 뒤 중복 제거해 정렬해 반환한다.
    quotes_by_symbol 이 있으면 등락·거래대금 등 시세 기반 RS 항이 반영된다(없으면 라이브·시총·수급 위주).
    """
    n = max(1, int(top_n))
    inv = investor_by_symbol or {}
    prog = program_by_symbol or {}
    qmap = dict(quotes_by_symbol or {})
    t0 = time.perf_counter()
    n_major = sum(1 for g in groups if str(g.get("group_type") or "") == "major")
    n_middle = sum(1 for g in groups if str(g.get("group_type") or "") == "middle")
    n_member_total = sum(len(list(g.get("stocks") or [])) for g in groups)
    n_quote_seed = len(qmap)
    print(
        "[probe] 1차 전체 탐색·프로브 시작 — "
        f"그룹 {len(groups)}개(대분류 {n_major} / 중분류 {n_middle}), "
        f"구성주 합계(중복 포함) {n_member_total}종, top_n={n}, "
        f"시세 맵(프로브 입력) {n_quote_seed}종, "
        f"라이브 랭킹 심볼 {len(live_signal_map)}개, "
        f"순위 수급 맵 {len(inv)}종"
        + (f", 프로그램 맵(프로브) {len(prog)}종" if prog else "")
    )
    out: set[str] = set()
    skipped_empty = 0
    progress_every = 50
    for gi, group in enumerate(groups, start=1):
        stocks = list(group.get("stocks") or [])
        if not stocks:
            skipped_empty += 1
            continue
        if len(groups) > progress_every and gi % progress_every == 0:
            print(f"[probe] 진행 {gi}/{len(groups)} 그룹…  누적 선정 심볼 {len(out)}개  {time.perf_counter() - t0:.2f}s")
        scored = _score_group_members(
            stocks,
            qmap,
            live_signal_map,
            investor_by_symbol=inv,
            program_by_symbol=prog,
        )
        for row in scored[:n]:
            sym = _norm_kis_stock_symbol(str(row.get("symbol", "") or ""))
            if sym:
                out.add(sym)
    elapsed = time.perf_counter() - t0
    print(
        "[probe] 1차 완료 — "
        f"{elapsed:.2f}s  "
        f"시세·프로그램 API 예정 심볼 {len(out)}개(그룹별 상위 top_n 합집합·중복 제거)"
        + (f"  구성 0종 스킵 그룹 {skipped_empty}개" if skipped_empty else "")
    )
    return sorted(out)


def _select_quote_symbols(
    groups: list[dict[str, Any]],
    live_signal_map: dict[str, dict[str, Any]],
    *,
    top_n: int,
    market_cap_fallback: int = 5,
    enrichment_mode: str = "top_by_group",
    investor_by_symbol: dict[str, dict[str, Any]] | None = None,
    program_by_symbol: dict[str, dict[str, Any]] | None = None,
    probe_quotes: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    """
    시세·프로그램 등 종목별 보강 API 호출 대상 심볼 목록.
    - enrichment_mode == 'top_by_group'(기본): 프로브 RS로 그룹당 top_n만 (대·중분류 각각).
    - enrichment_mode == 'all': 라이브 맵 키 + 모든 그룹 구성주 (기존 전체 보강).
    """
    if enrichment_mode == "all":
        print(
            "[probe] 1차 프로브 생략 — enrichment_mode=all "
            "(전 구성주 + 라이브 랭킹 심볼에 시세·프로그램 보강)"
        )
        symbols: set[str] = {_norm_kis_stock_symbol(s) for s in live_signal_map.keys() if _norm_kis_stock_symbol(s)}
        fallback_n = max(int(top_n), int(market_cap_fallback))
        for group in groups:
            stocks = list(group.get("stocks") or [])
            _ = fallback_n
            for stock in stocks:
                sym = _norm_kis_stock_symbol(stock.get("symbol", ""))
                if sym:
                    symbols.add(sym)
        out_all = sorted(symbols)
        print(
            f"[probe] all 모드 시세 대상 요약 — "
            f"고유 심볼 {len(out_all)}개 (라이브 키·전 그룹 구성주 합집합)"
        )
        return out_all
    if enrichment_mode != "top_by_group":
        raise ValueError(f"unknown enrichment_mode: {enrichment_mode!r}")
    return _probe_top_symbols_per_group(
        groups,
        live_signal_map,
        top_n=top_n,
        investor_by_symbol=investor_by_symbol,
        program_by_symbol=program_by_symbol,
        quotes_by_symbol=probe_quotes,
    )


def _score_group_members(
    stocks: list[dict[str, Any]],
    quotes_by_symbol: dict[str, dict[str, Any]],
    live_signal_map: dict[str, dict[str, Any]],
    *,
    investor_by_symbol: dict[str, dict[str, Any]] | None = None,
    program_by_symbol: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    inv = investor_by_symbol or {}
    prog = program_by_symbol or {}
    rows: list[dict[str, Any]] = []
    for stock in stocks:
        sym = _norm_kis_stock_symbol(stock.get("symbol", ""))
        quote = dict(quotes_by_symbol.get(sym) or {})
        live = dict(live_signal_map.get(sym) or {})
        iv = dict(inv.get(sym) or {})
        pg = dict(prog.get(sym) or {})
        rows.append(
            {
                "symbol": sym,
                "name": str(stock.get("name", "")).strip() or sym,
                "market_cap_eok": round(float(stock.get("market_cap_eok", 0.0)), 1),
                "price": quote.get("price"),
                "return_pct": quote.get("return_pct"),
                "value_traded": quote.get("value_traded"),
                "volume": quote.get("volume"),
                "foreign_net_tr_pbmn": iv.get("foreign_net_tr_pbmn"),
                "institution_net_tr_pbmn": iv.get("institution_net_tr_pbmn"),
                "program_net_tr_pbmn": pg.get("program_net_tr_pbmn"),
                "live_signal_score": float(live.get("live_signal_score", 0.0)),
                "live_signal_sources": dict(live.get("source_scores") or {}),
                "source_count": int(live.get("source_count", 0)),
            }
        )

    base_pct_map = _pct_rank_map([float(r.get("live_signal_score", 0.0)) for r in rows])
    market_cap_pct_map = _pct_rank_map([float(r.get("market_cap_eok", 0.0)) for r in rows])
    return_pct_map = _pct_rank_map([_safe_float(r.get("return_pct"), 0.0) for r in rows])
    value_pct_map = _pct_rank_map([_safe_float(r.get("value_traded"), 0.0) for r in rows])

    for row in rows:
        base_pct = base_pct_map.get(float(row.get("live_signal_score", 0.0)), 0.0)
        mcap_pct = market_cap_pct_map.get(float(row.get("market_cap_eok", 0.0)), 0.0)
        ret_pct = return_pct_map.get(_safe_float(row.get("return_pct"), 0.0), 0.0) if row.get("return_pct") is not None else 0.0
        value_pct = value_pct_map.get(_safe_float(row.get("value_traded"), 0.0), 0.0) if row.get("value_traded") is not None else 0.0
        trade_strength_pct = _safe_float(dict(row.get("live_signal_sources") or {}).get("trade_strength"), 0.0)
        near_high_pct = _safe_float(dict(row.get("live_signal_sources") or {}).get("near_high"), 0.0)
        row["rs"] = round(
            base_pct * STOCK_RS_W_LIVE
            + ret_pct * STOCK_RS_W_RETURN
            + value_pct * STOCK_RS_W_VALUE
            + trade_strength_pct * STOCK_RS_W_TRADE_STRENGTH
            + near_high_pct * STOCK_RS_W_NEAR_HIGH
            + mcap_pct * 0.00,
            1,
        )
        row["rs_components"] = {
            "live_signal_pct": round(base_pct, 1),
            "return_pct_rank": round(ret_pct, 1),
            "value_traded_pct": round(value_pct, 1),
            "trade_strength_pct": round(trade_strength_pct, 1),
            "near_high_pct": round(near_high_pct, 1),
            "market_cap_pct": round(mcap_pct, 1),
        }

    rows.sort(
        key=lambda x: (
            -float(x.get("rs", 0.0)),
            -_safe_float(x.get("return_pct"), 0.0),
            -_safe_float(x.get("value_traded"), 0.0),
            -_safe_float(dict(x.get("rs_components") or {}).get("return_pct_rank"), 0.0),
            -_safe_float(dict(x.get("rs_components") or {}).get("value_traded_pct"), 0.0),
            -_safe_float(dict(x.get("rs_components") or {}).get("trade_strength_pct"), 0.0),
            -_safe_float(dict(x.get("rs_components") or {}).get("near_high_pct"), 0.0),
            -float(x.get("live_signal_score", 0.0)),
            -float(x.get("market_cap_eok", 0.0)),
            str(x.get("name", "")),
        )
    )
    return rows


def _build_group_metrics(group: dict[str, Any], members: list[dict[str, Any]], top_members: list[dict[str, Any]]) -> dict[str, Any]:
    member_count = len(members)
    market_caps = [float(s.get("market_cap_eok", 0.0)) for s in members if float(s.get("market_cap_eok", 0.0)) > 0.0]
    rs_values = [float(s.get("rs", 0.0)) for s in members]
    total_cap = sum(market_caps)
    weighted_rs = (
        sum(float(s.get("rs", 0.0)) * float(s.get("market_cap_eok", 0.0)) for s in members) / total_cap
        if total_cap > 0
        else (sum(rs_values) / len(rs_values) if rs_values else 0.0)
    )
    high_rs_ratio = (sum(1 for v in rs_values if v >= 80.0) / len(rs_values)) if rs_values else 0.0
    strong_rs_ratio = (sum(1 for v in rs_values if v >= 60.0) / len(rs_values)) if rs_values else 0.0
    top_return_vals = [float(m.get("return_pct", 0.0)) for m in top_members if m.get("return_pct") not in (None, "")]
    top_value_traded = [_safe_float(m.get("value_traded"), 0.0) for m in top_members if _safe_float(m.get("value_traded"), 0.0) > 0.0]
    top_positive_ratio = (sum(1 for v in top_return_vals if v > 0) / len(top_return_vals)) if top_return_vals else 0.0
    top_avg_return = (sum(top_return_vals) / len(top_return_vals)) if top_return_vals else None
    top_value_share = (max(top_value_traded) / sum(top_value_traded)) if top_value_traded and sum(top_value_traded) > 0 else None
    top_members_value_sum = float(sum(top_value_traded)) if top_value_traded else 0.0
    total_value_traded = float(sum(_safe_float(m.get("value_traded"), 0.0) for m in members if _safe_float(m.get("value_traded"), 0.0) > 0.0))
    per_values = [x for stock in list(group.get("stocks") or []) for x in list(stock.get("per_values") or [])]
    avg_per = (sum(per_values) / len(per_values)) if per_values else None
    up_ratio = (
        (sum(1 for m in members if m.get("return_pct") is not None and float(m.get("return_pct") or 0.0) > 0.0) / len(members))
        if members
        else 0.0
    )
    rs60_ratio = (sum(1 for v in rs_values if v >= 60.0) / len(rs_values)) if rs_values else 0.0
    rs70_ratio = (sum(1 for v in rs_values if v >= 70.0) / len(rs_values)) if rs_values else 0.0

    def _pbmn_sum_eok(mems: list[dict[str, Any]], key: str) -> float | None:
        parts: list[float] = []
        for m in mems:
            v = m.get(key)
            if v is None:
                continue
            parts.append(float(v))
        return round(sum(parts) / 100_000_000.0, 1) if parts else None

    return {
        "weighted_rs": round(weighted_rs, 2),
        "avg_rs": round(sum(rs_values) / len(rs_values), 2) if rs_values else 0.0,
        "high_rs_ratio": round(high_rs_ratio * 100.0, 1),
        "strong_rs_ratio": round(strong_rs_ratio * 100.0, 1),
        "member_count": member_count,
        "market_cap_total_eok": round(total_cap, 1),
        "market_cap_log": math.log10(total_cap + 1.0),
        "top_members_value_sum": round(top_members_value_sum, 0),
        "total_value_traded": round(total_value_traded, 0),
        "top_members_foreign_net_eok_sum": _pbmn_sum_eok(top_members, "foreign_net_tr_pbmn"),
        "top_members_institution_net_eok_sum": _pbmn_sum_eok(top_members, "institution_net_tr_pbmn"),
        "top_members_program_net_eok_sum": _pbmn_sum_eok(top_members, "program_net_tr_pbmn"),
        "top_members_avg_return_pct": round(top_avg_return * 100.0, 2) if top_avg_return is not None else None,
        "top_members_positive_ratio": round(top_positive_ratio * 100.0, 1) if top_return_vals else None,
        "top_member_value_share_pct": round(float(top_value_share or 0.0) * 100.0, 1) if top_value_share is not None else None,
        "quote_coverage_ratio": round((sum(1 for m in members if m.get("return_pct") is not None) / len(members) * 100.0), 1) if members else 0.0,
        "live_signal_coverage_ratio": round((sum(1 for m in members if float(m.get("live_signal_score", 0.0)) > 0.0) / len(members) * 100.0), 1)
        if members
        else 0.0,
        "up_ratio": round(float(up_ratio), 3),
        "rs60_ratio": round(float(rs60_ratio), 3),
        "rs70_ratio": round(float(rs70_ratio), 3),
        "avg_per": round(avg_per, 2) if avg_per is not None else None,
    }


def _analyze_group_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return rows
    count_pct_map = _pct_rank_map([float(r["analysis"]["member_count"]) for r in rows])
    cap_pct_map = _pct_rank_map([float(r["analysis"]["market_cap_log"]) for r in rows])
    rep_avg_cohort_map = _pct_rank_map(
        [float(dict(r.get("analysis") or {}).get("top_members_avg_return_pct") or 0.0) for r in rows]
    )
    rep_value_sum_cohort_map = _pct_rank_map(
        [float(dict(r.get("analysis") or {}).get("top_members_value_sum") or 0.0) for r in rows]
    )
    live_cover_cohort_map = _pct_rank_map(
        [float(dict(r.get("analysis") or {}).get("live_signal_coverage_ratio") or 0.0) for r in rows]
    )
    scores = []
    for row in rows:
        a = dict(row.get("analysis") or {})
        rep_c = rep_avg_cohort_map.get(float(a.get("top_members_avg_return_pct") or 0.0), 0.0)
        val_c = rep_value_sum_cohort_map.get(float(a.get("top_members_value_sum") or 0.0), 0.0)
        live_c = live_cover_cohort_map.get(float(a.get("live_signal_coverage_ratio") or 0.0), 0.0)
        score = (
            rep_c * GROUP_SCORE_W_REP_RETURN_COHORT
            + val_c * GROUP_SCORE_W_REP_VALUE_SUM_COHORT
            + float(a.get("weighted_rs", 0.0)) * GROUP_SCORE_W_WEIGHTED_RS
            + float(a.get("avg_rs", 0.0)) * GROUP_SCORE_W_AVG_RS
            + float(a.get("high_rs_ratio", 0.0)) * GROUP_SCORE_W_HIGH_RS_SHARE
            + cap_pct_map.get(float(a.get("market_cap_log", 0.0)), 0.0) * GROUP_SCORE_W_CAP_RANK
            + count_pct_map.get(float(a.get("member_count", 0.0)), 0.0) * GROUP_SCORE_W_COUNT_RANK
            + live_c * GROUP_SCORE_W_LIVE_COVER_COHORT
        )
        scores.append(score)
    total = len(rows)
    cohort = str(rows[0].get("group_type") or "major")
    g_label = "중분류" if cohort == "middle" else "대분류"
    rank_order = sorted(
        range(total),
        key=lambda i: (-scores[i], str(rows[i].get("display_path", ""))),
    )
    rank_by_index = {orig_i: r + 1 for r, orig_i in enumerate(rank_order)}
    for idx, row in enumerate(rows):
        a = dict(row.get("analysis") or {})
        score = round(scores[idx], 1)
        signals = []
        if float(a.get("weighted_rs", 0.0)) >= 80.0:
            signals.append("시총가중 RS 강함")
        if float(a.get("avg_rs", 0.0)) >= 70.0:
            signals.append("평균 RS 높음")
        if float(a.get("high_rs_ratio", 0.0)) >= 30.0:
            signals.append("RS 80 이상 비중 높음")
        if float(a.get("strong_rs_ratio", 0.0)) >= 60.0:
            signals.append("RS 60 이상 종목층 두꺼움")
        if a.get("top_members_avg_return_pct") is not None and float(a.get("top_members_avg_return_pct", 0.0)) > 0:
            signals.append("대표 종목 당일 수익률 플러스")
        rep_cohort = rep_avg_cohort_map.get(float(a.get("top_members_avg_return_pct") or 0.0), 0.0)
        val_cohort = rep_value_sum_cohort_map.get(float(a.get("top_members_value_sum") or 0.0), 0.0)
        live_cov_cohort = live_cover_cohort_map.get(float(a.get("live_signal_coverage_ratio") or 0.0), 0.0)
        a["rep_top_return_cohort_pct"] = round(rep_cohort, 1)
        a["rep_top_value_sum_cohort_pct"] = round(val_cohort, 1)
        a["live_cover_cohort_pct"] = round(live_cov_cohort, 1)
        if rep_cohort >= 70.0 and a.get("top_members_avg_return_pct") is not None:
            signals.append("대표 평균 등락 코호트 상대 우수")
        if val_cohort >= 70.0 and float(a.get("top_members_value_sum") or 0.0) > 0:
            signals.append("대표 거래대금 합 코호트 상대 우수")
        if live_cov_cohort >= 70.0:
            signals.append("라이브 랭킹 커버 코호트 상대 우수")
        if a.get("top_members_positive_ratio") is not None and float(a.get("top_members_positive_ratio", 0.0)) >= 60.0:
            signals.append("대표 종목 상승 비중 우세")
        if a.get("top_member_value_share_pct") is not None and float(a.get("top_member_value_share_pct", 0.0)) <= 65.0:
            signals.append("대표 종목 거래대금 쏠림 완화")
        if score >= 78.0 and len(signals) >= 4:
            leader_status = "주도"
        elif score >= 60.0 and len(signals) >= 3:
            leader_status = "관심"
        elif score >= 45.0:
            leader_status = "중립"
        else:
            leader_status = "약세"
        a["relative_strength_score"] = score
        a["relative_strength_rank"] = rank_by_index[idx]
        a["relative_strength_total"] = total
        a["group_score_eval_scope"] = f"전체 {g_label} {total}개 그룹끼리만 비교(대분류/중분류 혼합 없음)"
        a["leader_status"] = leader_status
        a["leader_signal_count"] = len(signals)
        a["leader_signals"] = signals
        row["analysis"] = a
    rows.sort(
        key=lambda x: (
            -float(dict(x.get("analysis") or {}).get("relative_strength_score", 0.0)),
            str(x.get("display_path", "")),
        )
    )
    return rows


def _build_rows(
    groups: list[dict[str, Any]],
    quotes_by_symbol: dict[str, dict[str, Any]],
    live_signal_map: dict[str, dict[str, Any]],
    *,
    top_n: int,
    investor_by_symbol: dict[str, dict[str, Any]] | None = None,
    program_by_symbol: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rows = []
    for group in groups:
        all_members = _score_group_members(
            group["stocks"],
            quotes_by_symbol,
            live_signal_map,
            investor_by_symbol=investor_by_symbol,
            program_by_symbol=program_by_symbol,
        )
        members = all_members[:top_n]
        analysis = _build_group_metrics(group, all_members, members)
        n_peer = len(all_members)
        gt = str(group.get("group_type") or "")
        g_label = "중분류" if gt == "middle" else "대분류"
        # 종목 RS·백분위(_pct_rank)는 "이 그룹의 stocks 리스트" 안에서만 계산됨. 중분류는 표본이 작을 수 있어 체감이 달라질 수 있음.
        analysis["peer_member_count"] = n_peer
        analysis["is_small_group"] = n_peer < 8
        analysis["per_stock_eval_scope"] = f"{g_label} 「{group.get('display_path', '-')}」 구성 {n_peer}종목 내만 상대 비교"
        analysis["per_stock_stat_basis"] = "members_of_group_only"
        # 대표 종목 나열: major·middle 동일 — _score_group_members 정렬 후 앞에서 top_n개(그룹 종목이 적으면 그만큼만).
        analysis["top_stocks_requested"] = int(top_n)
        analysis["top_stocks_shown"] = len(members)
        analysis["top_stocks_peer_total"] = n_peer
        analysis["top_stocks_sort"] = "rs_desc_then_tie_break"
        rows.append(
            {
                "group_type": group["group_type"],
                "major_category": group["major_category"],
                "middle_category": group["middle_category"],
                "display_name": group["display_name"],
                "display_path": group["display_path"],
                "member_count": group["member_count"],
                "sub_category_count": group["sub_category_count"],
                "major_stocks": members,
                "analysis": analysis,
            }
        )
    return _analyze_group_rows(rows)


def _render_leaderboard_md(
    rows: list[dict[str, Any]],
    *,
    title: str,
    top_n: int = 5,
    top_k: int = 10,
    footnote: str = "",
) -> list[str]:
    lines = [f"## {title}", ""]
    if footnote:
        lines.append(f"> {footnote}")
        lines.append("")
    tpn_label = f"Top{int(top_n)} 평균"
    lines.extend(
        [
            f"| 순위 | 섹터 | RS | {tpn_label} | 상태 | 종목수 | 시총합(억) |",
            "| --- | --- | ---: | ---: | --- | ---: | ---: |",
        ]
    )
    for idx, row in enumerate(rows[:top_k], start=1):
        a = dict(row.get("analysis") or {})
        tma = a.get("top_members_avg_return_pct")
        tpn = (
            _fmt_pct(_safe_float(tma, 0.0) / 100.0) if tma is not None else "-"
        )
        lines.append(
            f"| {idx} | {row.get('display_path', '-')} | {a.get('relative_strength_score', '-')} | {tpn} | "
            f"{a.get('leader_status', '-')} | {a.get('member_count', '-')} | {_fmt_num(a.get('market_cap_total_eok'))} |"
        )
    lines.append("")
    return lines


def _render_leaderboard_md_v2(
    rows: list[dict[str, Any]],
    *,
    title: str,
    top_n: int = 5,
    top_k: int = 10,
    footnote: str = "",
) -> list[str]:
    lines = [f"## {title}", ""]
    if footnote:
        lines.append(f"> {footnote}")
        lines.append("")
    tpn_label = f"Top{int(top_n)} 평균"
    lines.extend(
        [
            f"| 순위 | 섹터 | ThemeScoreV2 | 기존RS | 지속성 | 확산도 | 품질 | {tpn_label} | 종목수 |",
            "| --- | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: |",
        ]
    )
    for idx, row in enumerate(rows[:top_k], start=1):
        a = dict(row.get("analysis") or {})
        tma = a.get("top_members_avg_return_pct")
        tpn = _fmt_pct(_safe_float(tma, 0.0) / 100.0) if tma is not None else "-"
        lines.append(
            f"| {idx} | {row.get('display_path', '-')} | {a.get('theme_score_v2', '-')} | {a.get('relative_strength_score', '-')} | "
            f"{a.get('persistence_score', '-')} | {a.get('breadth_score', '-')} | {a.get('theme_quality_label', '-')} | {tpn} | {a.get('member_count', '-')} |"
        )
    lines.append("")
    return lines


def _render_group_section_md(rows: list[dict[str, Any]], *, title: str) -> list[str]:
    lines = [f"## {title}", ""]
    if "중분류" in title:
        lines.append(
            "> **중분류:** 그룹 점수(리더보드 RS)는 **다른 중분류와만** 비교됩니다. "
            "각 **종목 RS·백분위**는 **그 중분류(single `middleCategory`)에만 묶인 구성원**으로만 계산됩니다(대분류와 섞이지 않음). "
            "하나의 종목이 복수 중분류에 중복돼도 **중분류마다 따로** 점수가 납니다."
        )
        lines.append("")
    elif "대분류" in title:
        lines.append(
            "> **대분류:** 그룹 점수는 **다른 대분류와만** 비교됩니다. "
            "각 **종목 RS·백분위**는 **그 대분류(하위 중분류 종목 합, 종목은 중복 제거) 구성원**으로만 계산됩니다."
        )
        lines.append("")
    for row in rows:
        a = dict(row.get("analysis") or {})
        lines.append(f"### {a.get('relative_strength_rank', '-')}위. {row.get('display_path', '-')}")
        lines.append("")
        lines.append(
            f"> RS: **{a.get('relative_strength_score', '-')}** ({a.get('relative_strength_rank', '-')}/{a.get('relative_strength_total', '-')})  "
            f"|  상태: **{a.get('leader_status', '-')}**  |  종목수: **{a.get('member_count', '-')}**"
        )
        lines.append("")
        # v2 quality metrics
        ql = str(a.get("theme_quality_label") or "").strip()
        if ql:
            lines.append(
                "- 테마 품질: **{}**  |  ThemeScoreV2: `{}`  |  지속성: `{}`  |  확산도: `{}`".format(
                    ql,
                    a.get("theme_score_v2", "-"),
                    a.get("persistence_score", "-") if a.get("persistence_score") is not None else "데이터부족",
                    a.get("breadth_score", "-") if a.get("breadth_score") is not None else "데이터부족",
                )
            )
            lines.append(
                "- Persistence: 5일 Top3 `{}` / 10일 Top5 `{}` / 5일 RS평균 `{}` / 5일 기울기 `{}` / 대금(5/20) `{}`".format(
                    f"{a.get('rank_top3_days_5d', 0)}일",
                    f"{a.get('rank_top5_days_10d', 0)}일",
                    a.get("rs_avg_5d", "-"),
                    a.get("rs_slope_5d", "-"),
                    (f"{a.get('value_ratio_5d_20d')}배" if a.get("value_ratio_5d_20d") is not None else "데이터부족"),
                )
            )
            lines.append(
                "- Breadth: RS60+ `{}` / 상대상승 `{}` / 대금확산 `{}`".format(
                    _fmt_pct(_safe_float(a.get("rs60_ratio"), 0.0)),
                    (f"{float(a.get('relative_up_ratio'))*100.0:+.1f}%p" if a.get("relative_up_ratio") is not None else "데이터부족"),
                    (f"{_fmt_pct(_safe_float(a.get('value_expansion_ratio'), 0.0))}" if a.get("value_expansion_ratio") is not None else "데이터부족"),
                )
            )
        lines.append(
            f"- 실시간 RS 지표: 시총가중 RS `{a.get('weighted_rs', '-')}` / 평균 RS `{a.get('avg_rs', '-')}` / "
            f"RS80+ 비중 `{_fmt_pct(_safe_float(a.get('high_rs_ratio'), 0.0) / 100.0)}` / "
            f"RS60+ 비중 `{_fmt_pct(_safe_float(a.get('strong_rs_ratio'), 0.0) / 100.0)}` / "
            f"대표등락·대금·라이브% `{a.get('rep_top_return_cohort_pct', '-')}` / "
            f"`{a.get('rep_top_value_sum_cohort_pct', '-')}` / `{a.get('live_cover_cohort_pct', '-')}` "
            f"(주도: 등락{int(GROUP_SCORE_W_REP_RETURN_COHORT * 100)}·대금{int(GROUP_SCORE_W_REP_VALUE_SUM_COHORT * 100)}%·합성RS{int((GROUP_SCORE_W_WEIGHTED_RS + GROUP_SCORE_W_AVG_RS + GROUP_SCORE_W_HIGH_RS_SHARE) * 100)}%·라이브{int(GROUP_SCORE_W_LIVE_COVER_COHORT * 100)}%)"
        )
        lines.append(
            f"- 규모 지표: 시총합 `{_fmt_num(a.get('market_cap_total_eok'))}억` / "
            f"대표종목 당일 평균 `{_fmt_pct(_safe_float(a.get('top_members_avg_return_pct'), 0.0) / 100.0) if a.get('top_members_avg_return_pct') is not None else '-'}` / "
            f"상승비중 `{_fmt_pct(_safe_float(a.get('top_members_positive_ratio'), 0.0) / 100.0) if a.get('top_members_positive_ratio') is not None else '-'}`"
        )
        tfe = a.get("top_members_foreign_net_eok_sum")
        tie = a.get("top_members_institution_net_eok_sum")
        tpe = a.get("top_members_program_net_eok_sum")
        if any(x is not None for x in (tfe, tie, tpe)):
            lines.append(
                "- 수급(대표 종목 순매수 합, 순매수거래대금 억 원): 외국인 `{}` · 기관 `{}` · 프로그램 `{}`".format(
                    f"{float(tfe):+,.1f}" if tfe is not None else "-",
                    f"{float(tie):+,.1f}" if tie is not None else "-",
                    f"{float(tpe):+,.1f}" if tpe is not None else "-",
                )
            )
        lines.append(
            f"- 신호 커버리지: 라이브 랭킹 `{_fmt_pct(_safe_float(a.get('live_signal_coverage_ratio'), 0.0) / 100.0)}` / "
            f"현재가 보강 `{_fmt_pct(_safe_float(a.get('quote_coverage_ratio'), 0.0) / 100.0)}`"
        )
        if row.get("sub_category_count"):
            lines.append(f"- 하위 중분류 수: `{row.get('sub_category_count')}`")
        if a.get("per_stock_eval_scope"):
            lines.append(f"- 종목 RS·백분위 범위: {a.get('per_stock_eval_scope')}")
        if a.get("top_stocks_shown") is not None and a.get("top_stocks_requested") is not None:
            ts_req = a.get("top_stocks_requested")
            ts_peer = a.get("top_stocks_peer_total")
            lines.append(
                f"- 대표 종목 나열: 그룹 내 RS 상위 **{a.get('top_stocks_shown')}**종 표시 "
                f"(요청 상한 `top_n={ts_req}`, 이 그룹 종목 수 `{ts_peer}` — "
                f"부족하면 가능한 만큼만)"
            )
        if a.get("is_small_group"):
            lines.append(
                f"- 구성 {a.get('peer_member_count')}-종(소표본): RS·대표가 요동일 수 있음. 동일 티커가 **서로 다른 중분류**에 있으면 점수는 **각 그룹에서 따로** 계산됨"
            )
        if int(a.get("member_count") or 0) < 10:
            lines.append("> 표본 종목 수가 적어 확산도 점수 변동성이 클 수 있음.")
        if a.get("avg_per") is not None:
            lines.append(f"- PER 평균(단순): `{a.get('avg_per')}`")
        signals = list(a.get("leader_signals") or [])
        if signals:
            lines.append(f"- 주도 신호: {', '.join(signals)}")
        lines.append("")
        lines.append(
            "| 대표 종목 | 코드 | RS | 시총(억) | 당일등락률 | 현재가 | 거래대금(억) | 외국인 순매수(억) | 기관 순매수(억) | 프로그램 순매수(억) |"
        )
        lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for m in row.get("major_stocks") or []:
            lines.append(
                f"| {m.get('name', '-')} | `{m.get('symbol', '-')}` | {m.get('rs', '-')} | "
                f"{_fmt_num(m.get('market_cap_eok'))} | {_fmt_pct(m.get('return_pct'))} | {_fmt_num(m.get('price'))} | {_fmt_eok(m.get('value_traded'))} | "
                f"{_fmt_signed_eok(m.get('foreign_net_tr_pbmn'))} | {_fmt_signed_eok(m.get('institution_net_tr_pbmn'))} | {_fmt_signed_eok(m.get('program_net_tr_pbmn'))} |"
            )
        lines.append("")
    return lines


def _render_thema_summary_md(
    *,
    source_path: Path,
    collected_at: str,
    meta: dict[str, Any],
    major_rows: list[dict[str, Any]],
    middle_rows: list[dict[str, Any]],
    top_n: int = 5,
) -> str:
    lines = [
        "# Thema Major/Middle Sector Overview",
        "",
        f"> 분류 원본: {source_path}",
        f"> 생성 시각: {_fmt_ts(collected_at)}",
        f"> 대분류 수: {meta.get('major_category_count', '-')} / 중분류 수: {meta.get('middle_category_count', '-')}",
        f"> 종목 수: {meta.get('unique_stock_count', '-')} / 시세 보강 종목 수: {meta.get('quote_symbol_count', '-')}"
        f" / 외국인·기관 순위 집계 심볼: {meta.get('foreign_institution_rank_symbol_count', '-')} / 프로그램(종목별) 심볼: {meta.get('program_trade_symbol_count', '-')}"
        + (
            f" / 시세·프로그램 API 대상: {meta.get('quote_enrichment_api_target_count', '-')}종 (`{meta.get('quote_enrichment_mode', '-')}`)"
            if meta.get("quote_enrichment_mode") is not None
            else ""
        ),
        "",
        "<details open>",
        "<summary>대분류 Leaderboard</summary>",
        "",
    ]
    lines.extend(
        _render_leaderboard_md(
            major_rows,
            title="대분류 Leaderboard",
            top_n=top_n,
            footnote="그룹 점수(표 RS)는 **다른 대분류와만** 비교. 종목 RS·백분위·대표는 **해당 대분류 구성(하위 전체, 종목 중복 제거) 안에서만** 상대 비교.",
        )
    )
    lines.extend(
        _render_leaderboard_md_v2(
            major_rows,
            title="대분류 Leaderboard (ThemeScoreV2)",
            top_n=top_n,
            footnote="ThemeScoreV2 = 기존RS(60%) + Persistence(20%) + Breadth(20%). (데이터 부족 시 품질/점수는 '데이터부족')",
        )
    )
    lines.extend(["</details>", "", "<details>", "<summary>중분류 Leaderboard</summary>", ""])
    lines.extend(
        _render_leaderboard_md(
            middle_rows,
            title="중분류 Leaderboard",
            top_n=top_n,
            footnote="그룹 점수는 **다른 중분류와만** 비교(대분류 랭킹과 별개). "
            "종목 RS·백분위·대표는 **그 중분류(single `middleCategory`)에만 싣은 구성** 안에서만 상대 비교. 표본(종목 수)이 적으면 지표가 요동칠 수 있음.",
        )
    )
    lines.extend(
        _render_leaderboard_md_v2(
            middle_rows,
            title="중분류 Leaderboard (ThemeScoreV2)",
            top_n=top_n,
            footnote="ThemeScoreV2 = 기존RS(60%) + Persistence(20%) + Breadth(20%). (데이터 부족 시 품질/점수는 '데이터부족')",
        )
    )
    lines.extend(["</details>", "", "<details open>", "<summary>대분류 섹터 카드</summary>", ""])
    lines.extend(_render_group_section_md(major_rows, title="대분류 섹터 카드"))
    lines.extend(["</details>", "", "<details>", "<summary>중분류 섹터 카드</summary>", ""])
    lines.extend(_render_group_section_md(middle_rows, title="중분류 섹터 카드"))
    lines.append("</details>")
    return "\n".join(lines).rstrip() + "\n"


def _leader_cls(status: str) -> str:
    if status == "주도":
        return "leader-yes"
    if status == "관심":
        return "leader-watch"
    return "leader-flat"


def _render_leaderboard_html(
    rows: list[dict[str, Any]],
    *,
    title: str,
    top_n: int,
    open_by_default: bool,
    footnote: str = "",
) -> str:
    open_attr = " open" if open_by_default else ""
    tpn_h = f"Top{int(top_n)} 평균"
    parts = [f"<details class=\"section fold\"{open_attr}><summary class=\"section-title\">{_escape_html(title)}</summary><div class=\"leaderboard\">"]
    parts.append(
        f"<table class=\"leaderboard-table\"><thead><tr><th>순위</th><th>섹터</th><th>RS</th><th>{_escape_html(tpn_h)}</th>"
        "<th>상태</th><th>종목수</th><th>시총합(억)</th></tr></thead><tbody>"
    )
    for idx, row in enumerate(rows[:10], start=1):
        a = dict(row.get("analysis") or {})
        tma = a.get("top_members_avg_return_pct")
        if tma is not None:
            tpn_html = _signed_value_html(
                _fmt_pct(_safe_float(tma, 0.0) / 100.0),
                _safe_float(tma, 0.0) / 100.0,
            )
        else:
            tpn_html = "-"
        parts.append(
            "<tr>"
            f"<td>{idx}</td>"
            f"<td>{_escape_html(row.get('display_path', '-'))}</td>"
            f"<td>{a.get('relative_strength_score', '-')}</td>"
            f"<td>{tpn_html}</td>"
            f"<td>{_escape_html(a.get('leader_status', '-'))}</td>"
            f"<td>{a.get('member_count', '-')}</td>"
            f"<td>{_fmt_num(a.get('market_cap_total_eok'))}</td>"
            "</tr>"
        )
    parts.append("</tbody></table>")
    if footnote:
        parts.append(f"<p class=\"lb-footnote\">{_escape_html(footnote)}</p>")
    parts.append("</div></details>")
    return "".join(parts)


def _render_leaderboard_html_v2(
    rows: list[dict[str, Any]],
    *,
    title: str,
    top_n: int,
    open_by_default: bool,
    footnote: str = "",
) -> str:
    open_attr = " open" if open_by_default else ""
    tpn_h = f"Top{int(top_n)} 평균"
    parts = [f"<details class=\"section fold\"{open_attr}><summary class=\"section-title\">{_escape_html(title)}</summary><div class=\"leaderboard\">"]
    parts.append(
        "<table class=\"leaderboard-table\"><thead><tr>"
        "<th>순위</th><th>섹터</th><th>ThemeScoreV2</th><th>기존RS</th><th>지속성</th><th>확산도</th><th>테마품질</th>"
        f"<th>{_escape_html(tpn_h)}</th><th>종목수</th>"
        "</tr></thead><tbody>"
    )
    for idx, row in enumerate(rows[:10], start=1):
        a = dict(row.get("analysis") or {})
        tma = a.get("top_members_avg_return_pct")
        if tma is not None:
            tpn_html = _signed_value_html(
                _fmt_pct(_safe_float(tma, 0.0) / 100.0),
                _safe_float(tma, 0.0) / 100.0,
            )
        else:
            tpn_html = "-"
        parts.append(
            "<tr>"
            f"<td>{idx}</td>"
            f"<td>{_escape_html(row.get('display_path', '-'))}</td>"
            f"<td>{_escape_html(str(a.get('theme_score_v2', '-') if a.get('theme_score_v2') is not None else '데이터부족'))}</td>"
            f"<td>{_escape_html(str(a.get('relative_strength_score', '-')))}</td>"
            f"<td>{_escape_html(str(a.get('persistence_score', '-') if a.get('persistence_score') is not None else '데이터부족'))}</td>"
            f"<td>{_escape_html(str(a.get('breadth_score', '-') if a.get('breadth_score') is not None else '데이터부족'))}</td>"
            f"<td>{_escape_html(str(a.get('theme_quality_label', '-') or '-'))}</td>"
            f"<td>{tpn_html}</td>"
            f"<td>{_escape_html(str(a.get('member_count', '-')))}</td>"
            "</tr>"
        )
    parts.append("</tbody></table>")
    if footnote:
        parts.append(f"<p class=\"lb-footnote\">{_escape_html(footnote)}</p>")
    parts.append("</div></details>")
    return "".join(parts)


def _sign_class(value: Any) -> str:
    v = _safe_float(value, 0.0)
    if v > 0:
        return "pos"
    if v < 0:
        return "neg"
    return "neutral"


def _signed_value_html(text: str, sign_value: Any) -> str:
    return f"<span class=\"{_sign_class(sign_value)}\">{_escape_html(text)}</span>"


def _render_group_cards_html(rows: list[dict[str, Any]], *, title: str, open_by_default: bool) -> str:
    open_attr = " open" if open_by_default else ""
    parts = [f"<details class=\"section fold\"{open_attr}><summary class=\"section-title\">{_escape_html(title)}</summary><div class=\"grid\">"]
    for idx, row in enumerate(rows):
        a = dict(row.get("analysis") or {})
        parts.append(f"<details class=\"card\"{' open' if idx == 0 else ''}>")
        parts.append("<summary class=\"card-head card-summary\">")
        parts.append("<div>")
        parts.append(f"<h2>{_escape_html(row.get('display_name', '-'))}</h2>")
        parts.append(f"<div class=\"sub\">{_escape_html(row.get('display_path', '-'))}</div>")
        parts.append("<div class=\"meta\">")
        parts.append(f"<span class=\"pill {_leader_cls(str(a.get('leader_status', '-')))}\">{_escape_html(a.get('leader_status', '-'))}</span>")
        parts.append(f"<span class=\"pill\">종목수 {a.get('member_count', '-')}</span>")
        if row.get("sub_category_count"):
            parts.append(f"<span class=\"pill\">중분류 {row.get('sub_category_count')}</span>")
        if a.get("avg_per") is not None:
            parts.append(f"<span class=\"pill\">PER {a.get('avg_per')}</span>")
        parts.append("</div></div>")
        parts.append("<div class=\"rs-box\">")
        parts.append(f"<div class=\"rs-score\">{float(_safe_float(a.get('relative_strength_score'), 0.0)):.1f}</div>")
        parts.append(
            f"<div class=\"rs-rank\">RS Rank #{a.get('relative_strength_rank', '-')} / {a.get('relative_strength_total', '-')}</div>"
        )
        parts.append(f"<div class=\"rs-bar\"><div class=\"rs-fill\" style=\"width:{max(0.0, min(100.0, float(_safe_float(a.get('relative_strength_score'), 0.0)))):.1f}%\"></div></div>")
        parts.append("</div></summary>")
        parts.append("<div class=\"card-body\">")
        if a.get("per_stock_eval_scope"):
            parts.append(f"<p class=\"eval-scope\">{_escape_html(str(a.get('per_stock_eval_scope')))}</p>")
        if a.get("is_small_group"):
            parts.append(
                f"<p class=\"eval-warn\">구성 {a.get('peer_member_count')}종(소표본) — "
                f"RS·백분위·대표가 요동일 수 있음. 동일 티커·다른 중분류는 점수가 각각 산출됨.</p>"
            )
        if a.get("top_stocks_shown") is not None and a.get("top_stocks_requested") is not None:
            parts.append(
                "<p class=\"eval-topn\">"
                f"대표 종목: 그룹 RS 상위 <strong>{a.get('top_stocks_shown')}</strong>종 "
                f"(요청 상한 {a.get('top_stocks_requested')} / 그룹 {a.get('top_stocks_peer_total')}종 — "
                "구성이 적으면 그만큼만 표시)"
                "</p>"
            )
        parts.append("<div class=\"metrics\">")
        metric_pairs = [
            ("테마 품질", a.get("theme_quality_label") or "-", None),
            ("ThemeScoreV2", a.get("theme_score_v2") if a.get("theme_score_v2") is not None else "데이터부족", None),
            ("지속성", a.get("persistence_score") if a.get("persistence_score") is not None else "데이터부족", None),
            ("확산도", a.get("breadth_score") if a.get("breadth_score") is not None else "데이터부족", None),
            ("5일 Top3", f"{int(a.get('rank_top3_days_5d') or 0)}일" if a.get("persistence_score") is not None else "데이터부족", None),
            ("시총가중 RS", a.get("weighted_rs"), None),
            ("평균 RS", a.get("avg_rs"), None),
            ("RS80+ 비중", _fmt_pct(_safe_float(a.get("high_rs_ratio"), 0.0) / 100.0), None),
            ("RS60+ 비중", _fmt_pct(_safe_float(a.get("strong_rs_ratio"), 0.0) / 100.0), None),
            ("RS60+ 비중(정의)", _fmt_pct(_safe_float(a.get("rs60_ratio"), 0.0)), None),
            ("상대 상승비율", f"{float(_safe_float(a.get('relative_up_ratio'), 0.0))*100.0:+.1f}%p" if a.get("relative_up_ratio") is not None else "데이터부족", None),
            ("거래대금 확산비율", _fmt_pct(_safe_float(a.get("value_expansion_ratio"), 0.0)) if a.get("value_expansion_ratio") is not None else "데이터부족", None),
            ("대표등락 코호트%", a.get("rep_top_return_cohort_pct"), None),
            ("대표대금합 코호트%", a.get("rep_top_value_sum_cohort_pct"), None),
            ("라이브커버 코호트%", a.get("live_cover_cohort_pct"), None),
            ("시총합(억)", _fmt_num(a.get("market_cap_total_eok")), None),
            ("라이브 랭킹 커버", _fmt_pct(_safe_float(a.get("live_signal_coverage_ratio"), 0.0) / 100.0), None),
            ("현재가 보강 커버", _fmt_pct(_safe_float(a.get("quote_coverage_ratio"), 0.0) / 100.0), None),
            (
                "대표 당일 평균",
                _fmt_pct(_safe_float(a.get("top_members_avg_return_pct"), 0.0) / 100.0)
                if a.get("top_members_avg_return_pct") is not None
                else "-",
                a.get("top_members_avg_return_pct"),
            ),
            (
                "대표 상승 비중",
                _fmt_pct(_safe_float(a.get("top_members_positive_ratio"), 0.0) / 100.0)
                if a.get("top_members_positive_ratio") is not None
                else "-",
                None,
            ),
            (
                "대표 거래쏠림",
                _fmt_pct(_safe_float(a.get("top_member_value_share_pct"), 0.0) / 100.0)
                if a.get("top_member_value_share_pct") is not None
                else "-",
                None,
            ),
            ("대표 외국인 순매수 합", f"{float(a['top_members_foreign_net_eok_sum']):+,.1f}" if a.get("top_members_foreign_net_eok_sum") is not None else "-", a.get("top_members_foreign_net_eok_sum")),
            ("대표 기관 순매수 합", f"{float(a['top_members_institution_net_eok_sum']):+,.1f}" if a.get("top_members_institution_net_eok_sum") is not None else "-", a.get("top_members_institution_net_eok_sum")),
            ("대표 프로그램 순매수 합", f"{float(a['top_members_program_net_eok_sum']):+,.1f}" if a.get("top_members_program_net_eok_sum") is not None else "-", a.get("top_members_program_net_eok_sum")),
        ]
        for label, value, sign_value in metric_pairs:
            value_html = _signed_value_html(str(value), sign_value) if sign_value is not None and value != "-" else _escape_html(value)
            parts.append(f"<div class=\"metric\"><div class=\"k\">{_escape_html(label)}</div><div class=\"v\">{value_html}</div></div>")
        parts.append("</div>")
        signals = list(a.get("leader_signals") or [])
        if signals:
            parts.append("<div class=\"signal-list\">")
            for signal in signals:
                parts.append(f"<span class=\"signal\">{_escape_html(signal)}</span>")
            parts.append("</div>")
        parts.append("<table>")
        parts.append(
            "<thead><tr><th>종목</th><th>코드</th><th>RS</th><th>시총(억)</th>"
            "<th>당일등락률</th><th>현재가</th><th>거래대금(억)</th>"
            "<th>외국인 순매수(억)</th><th>기관 순매수(억)</th><th>프로그램 순매수(억)</th></tr></thead><tbody>"
        )
        for m in row.get("major_stocks") or []:
            url = _naver_finance_stock_url(str(m.get("symbol", "")))
            fv = _fmt_signed_eok(m.get("foreign_net_tr_pbmn"))
            iv = _fmt_signed_eok(m.get("institution_net_tr_pbmn"))
            pv = _fmt_signed_eok(m.get("program_net_tr_pbmn"))
            parts.append(
                "<tr>"
                f"<td><a href=\"{_escape_html(url)}\" target=\"_blank\" rel=\"noopener noreferrer\">{_escape_html(m.get('name', '-'))}</a></td>"
                f"<td><a href=\"{_escape_html(url)}\" target=\"_blank\" rel=\"noopener noreferrer\">{_escape_html(m.get('symbol', '-'))}</a></td>"
                f"<td>{_escape_html(m.get('rs', '-'))}</td>"
                f"<td>{_fmt_num(m.get('market_cap_eok'))}</td>"
                f"<td>{_signed_value_html(_fmt_pct(m.get('return_pct')), m.get('return_pct')) if m.get('return_pct') is not None else '-'}</td>"
                f"<td>{_signed_value_html(_fmt_num(m.get('price')), m.get('return_pct')) if m.get('price') is not None else '-'}</td>"
                f"<td>{_fmt_eok(m.get('value_traded'))}</td>"
                f"<td>{_signed_value_html(fv, m.get('foreign_net_tr_pbmn')) if fv != '-' else '-'}</td>"
                f"<td>{_signed_value_html(iv, m.get('institution_net_tr_pbmn')) if iv != '-' else '-'}</td>"
                f"<td>{_signed_value_html(pv, m.get('program_net_tr_pbmn')) if pv != '-' else '-'}</td>"
                "</tr>"
            )
        parts.append("</tbody></table>")
        parts.append("</div></details>")
    parts.append("</div></details>")
    return "".join(parts)


def _render_thema_report_html(
    *,
    source_path: Path,
    collected_at: str,
    meta: dict[str, Any],
    major_rows: list[dict[str, Any]],
    middle_rows: list[dict[str, Any]],
    top_n: int,
) -> str:
    leader_count = sum(1 for row in major_rows + middle_rows if dict(row.get("analysis") or {}).get("leader_status") == "주도")
    return (
        "<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>Thema Major/Middle Sector Overview</title>"
        "<style>"
        ":root{--bg:#0b1020;--panel:#ffffff;--panel-soft:#f4f7fb;--text:#111827;--muted:#667085;--line:#e5e7eb;--leader:#0ea5e9;--watch:#f59e0b;}"
        "body{font-family:Segoe UI,Arial,sans-serif;margin:0;background:linear-gradient(180deg,#0b1020 0,#10182d 280px,#eef3f8 280px,#eef3f8 100%);color:var(--text);}"
        ".wrap{max-width:1480px;margin:0 auto;padding:28px 24px 48px;}.hero{background:linear-gradient(135deg,#0f172a,#1d4ed8 60%,#0ea5e9);color:#fff;border-radius:24px;padding:28px 32px;box-shadow:0 20px 40px rgba(15,23,42,.22);}"
        ".hero h1{margin:0 0 10px;font-size:32px;}.hero p{margin:0;color:rgba(255,255,255,.86);font-size:14px;line-height:1.6;}"
        ".hero-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-top:18px;}.hero-stat{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.18);border-radius:16px;padding:14px 16px;}"
        ".hero-stat .label{font-size:12px;color:rgba(255,255,255,.75);}.hero-stat .value{margin-top:6px;font-size:20px;font-weight:700;}"
        ".section{margin-top:24px;}.section-title{margin:0;color:#0f172a;font-size:18px;font-weight:700;cursor:pointer;list-style:none;padding:16px 18px;background:#fff;border:1px solid rgba(15,23,42,.06);border-radius:18px;box-shadow:0 10px 24px rgba(15,23,42,.06);}"
        ".section-title::-webkit-details-marker,.card-summary::-webkit-details-marker{display:none;}.fold[open] > .section-title{border-bottom-left-radius:0;border-bottom-right-radius:0;margin-bottom:0;}"
        ".leaderboard,.card{background:var(--panel);border:1px solid rgba(15,23,42,.06);border-radius:22px;box-shadow:0 14px 34px rgba(15,23,42,.08);}"
        ".leaderboard{padding:18px 20px;border-top-left-radius:0;border-top-right-radius:0;}.lb-footnote{margin:10px 0 0;padding:0 4px;font-size:12px;color:var(--muted);line-height:1.45;}"
        ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(430px,1fr));gap:18px;margin-top:14px;}.card{position:relative;overflow:hidden;}"
        ".eval-scope{margin:0 0 10px;padding:10px 12px;border-radius:12px;background:#f0f6ff;border:1px solid #bfdbfe;font-size:12px;color:#1e3a5f;line-height:1.45;}"
        ".eval-warn{margin:0 0 10px;padding:10px 12px;border-radius:12px;background:#fff8f0;border:1px solid #fcd9b8;font-size:12px;color:#7c2d12;line-height:1.45;}"
        ".eval-topn{margin:0 0 10px;padding:8px 12px;border-radius:10px;background:#f8fafc;border:1px solid #e2e8f0;font-size:12px;color:#334155;line-height:1.45;}"
        ".card::before{content:'';position:absolute;inset:0 auto auto 0;width:100%;height:4px;background:linear-gradient(90deg,#38bdf8,#2563eb);}"
        ".card-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;}.card-summary{padding:18px;cursor:pointer;list-style:none;}.card-body{padding:0 18px 18px;}.card h2{margin:0;font-size:22px;}.sub{margin-top:4px;color:var(--muted);font-size:12px;}"
        ".meta,.metrics,.signal-list{display:flex;flex-wrap:wrap;gap:8px;}.pill{font-size:12px;padding:6px 10px;border-radius:999px;background:#f3f6fb;border:1px solid #dbe3ef;color:#334155;}.leader-yes{background:#dcfce7;border-color:#86efac;color:#166534;}.leader-watch{background:#fef3c7;border-color:#fcd34d;color:#92400e;}.leader-flat{background:#eef2f7;border-color:#d0d7e2;color:#475467;}"
        ".rs-box{text-align:right;min-width:110px;}.rs-score{font-size:28px;font-weight:800;color:#0f172a;line-height:1;}.rs-rank{margin-top:6px;color:var(--muted);font-size:12px;}.rs-bar{margin-top:10px;height:8px;border-radius:999px;background:#e6edf7;overflow:hidden;}.rs-fill{height:100%;background:linear-gradient(90deg,#38bdf8,#2563eb);border-radius:999px;}"
        ".metrics{margin:14px 0 12px;}.metric{flex:1 1 160px;background:var(--panel-soft);border:1px solid var(--line);border-radius:16px;padding:12px;}.metric .k{font-size:12px;color:var(--muted);}.metric .v{margin-top:6px;font-size:18px;font-weight:700;}"
        ".signal-list{margin-top:10px;}.signal{font-size:12px;padding:6px 10px;border-radius:999px;background:#eff6ff;border:1px solid #bfdbfe;color:#1d4ed8;}"
        ".pos{color:#d92d20;font-weight:700;}.neg{color:#0969da;font-weight:700;}.neutral{color:#667085;font-weight:600;}"
        "table{width:100%;border-collapse:separate;border-spacing:0;font-size:13px;margin-top:14px;overflow:hidden;border:1px solid var(--line);border-radius:16px;}th,td{padding:10px 10px;border-top:1px solid var(--line);text-align:right;background:#fff;}thead th{background:#f8fafc;border-top:none;color:#334155;font-weight:700;}tbody tr:nth-child(even) td{background:#fbfdff;}th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left;}.leaderboard-table th,.leaderboard-table td{text-align:left;}.leaderboard-table th:nth-child(3),.leaderboard-table td:nth-child(3),.leaderboard-table th:nth-child(4),.leaderboard-table td:nth-child(4),.leaderboard-table th:nth-child(5),.leaderboard-table td:nth-child(5),.leaderboard-table th:nth-child(6),.leaderboard-table td:nth-child(6),.leaderboard-table th:nth-child(7),.leaderboard-table td:nth-child(7){text-align:right;}"
        "a{color:#1d4ed8;text-decoration:none;}a:hover{text-decoration:underline;}@media (max-width:720px){.wrap{padding:18px 14px 36px;}.hero{padding:22px 20px;}.card-head{flex-direction:column;}.rs-box{text-align:left;}}"
        "</style></head><body><div class=\"wrap\">"
        "<section class=\"hero\">"
        "<h1>Thema Major/Middle Sector Overview</h1>"
        f"<p>분류 원본: {_escape_html(source_path)}<br>생성 시각: {_escape_html(_fmt_ts(collected_at))}<br>"
        f"대분류 {meta.get('major_category_count', '-')} / 중분류 {meta.get('middle_category_count', '-')} / "
        f"종목 {meta.get('unique_stock_count', '-')} / 라이브 신호 {meta.get('live_signal_symbol_count', '-')} / "
        f"시세보강 {meta.get('quote_symbol_count', '-')} / 수급(KIS 순위) {meta.get('foreign_institution_rank_symbol_count', '-')} / "
        f"프로그램 종목별 {meta.get('program_trade_symbol_count', '-')} / 대표종목 {top_n}</p>"
        "<div class=\"hero-stats\">"
        f"<div class=\"hero-stat\"><div class=\"label\">대분류</div><div class=\"value\">{len(major_rows)}</div></div>"
        f"<div class=\"hero-stat\"><div class=\"label\">중분류</div><div class=\"value\">{len(middle_rows)}</div></div>"
        f"<div class=\"hero-stat\"><div class=\"label\">주도 그룹</div><div class=\"value\">{leader_count}</div></div>"
        f"<div class=\"hero-stat\"><div class=\"label\">분류 스키마</div><div class=\"value\">{_escape_html(meta.get('schema_version', '-'))}</div></div>"
        "</div></section>"
        f"{_render_leaderboard_html(major_rows, title='대분류 Leaderboard', top_n=top_n, open_by_default=True, footnote='그룹 점수는 다른 대분류와만 비교. 종목 RS·백분위·대표는 해당 대분류(하위 전체, 종목 중복 제거) 구성만 기준.')}"
        f"{_render_leaderboard_html_v2(major_rows, title='대분류 Leaderboard (ThemeScoreV2)', top_n=top_n, open_by_default=False, footnote='ThemeScoreV2 = 기존RS(60%) + Persistence(20%) + Breadth(20%).')}"
        f"{_render_leaderboard_html(middle_rows, title='중분류 Leaderboard', top_n=top_n, open_by_default=False, footnote='그룹 점수는 다른 중분류와만 비교(대분류 랭킹과 별도). 종목 RS·백분위·대표는 그 중분류에만 싣은 구성으로만. 표본이 적은 중분류는 지표가 요동칠 수 있음.')}"
        f"{_render_leaderboard_html_v2(middle_rows, title='중분류 Leaderboard (ThemeScoreV2)', top_n=top_n, open_by_default=False, footnote='ThemeScoreV2 = 기존RS(60%) + Persistence(20%) + Breadth(20%).')}"
        f"{_render_group_cards_html(major_rows, title='대분류 섹터 카드', open_by_default=True)}"
        f"{_render_group_cards_html(middle_rows, title='중분류 섹터 카드', open_by_default=False)}"
        "</div></body></html>"
    )


def _telegram_summary(collected_at: str, major_rows: list[dict[str, Any]], middle_rows: list[dict[str, Any]]) -> str:
    lines = [
        "Thema 대/중분류 섹터 리포트 생성 완료",
        f"- 생성 시각: {_fmt_ts(collected_at)}",
        "- 대분류 상위 3:",
    ]
    for row in major_rows[:3]:
        a = dict(row.get("analysis") or {})
        lines.append(f"  · {row.get('display_name')}: RS {a.get('relative_strength_score')} / {a.get('leader_status')}")
    lines.append("- 중분류 상위 5:")
    for row in middle_rows[:5]:
        a = dict(row.get("analysis") or {})
        lines.append(f"  · {row.get('display_path')}: RS {a.get('relative_strength_score')} / {a.get('leader_status')}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Thema 대분류/중분류 기반 섹터 리포트 생성")
    parser.add_argument("--mode", choices=("paper", "real"), default="real", help="KIS mode for quote enrichment.")
    parser.add_argument(
        "--classification-json",
        type=Path,
        default=DEFAULT_CLASSIFICATION_JSON,
        help="Thema classification JSON path.",
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output directory.")
    parser.add_argument("--theme-history-dir", type=Path, default=DEFAULT_THEME_HISTORY_DIR, help="Theme snapshot history directory.")
    parser.add_argument("--top-n", type=int, default=5, help="Top stocks per group.")
    parser.add_argument(
        "--no-quote-enrichment",
        action="store_true",
        help="KIS 현재가/거래대금/종목 프로그램 조회 생략(라이브 랭킹·외국인·기관 순위 가능 범위만 빠르게).",
    )
    parser.add_argument(
        "--full-quote-enrichment",
        action="store_true",
        help="모든 구성주(+라이브 랭킹에 나온 심볼)에 시세·프로그램 보강. 기본은 섹터별 프로브 RS 상위 top-n만 보강.",
    )
    parser.add_argument(
        "--enrichment-workers",
        type=int,
        default=4,
        help="보강(시세·프로그램·필요 시 종목별 수급) 병렬 워커 수. 기본 4. 과도한 값은 KIS 제한에 걸릴 수 있음.",
    )
    parser.add_argument("--telegram", action="store_true", help="Send summary + html report to Telegram.")
    parser.add_argument(
        "--telegram-bot-token",
        default=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        help="Telegram bot token (default: TELEGRAM_BOT_TOKEN env).",
    )
    parser.add_argument(
        "--telegram-chat-id",
        default=os.getenv("TELEGRAM_CHAT_ID_STOCK", ""),
        help="Telegram chat id (default: TELEGRAM_CHAT_ID_STOCK env).",
    )
    parser.add_argument(
        "--telegram-thread-id",
        default=os.getenv("TELEGRAM_MESSAGE_THREAD_ID", ""),
        help="Optional Telegram message thread id.",
    )
    args = parser.parse_args()
    if bool(args.no_quote_enrichment) and bool(args.full_quote_enrichment):
        raise SystemExit("--no-quote-enrichment 와 --full-quote-enrichment 는 함께 쓸 수 없습니다.")
    quote_enrichment = not bool(args.no_quote_enrichment)
    quote_enrichment_mode = "all" if bool(args.full_quote_enrichment) else "top_by_group"

    source_path = _resolve_classification_json(Path(args.classification_json))
    if not source_path.is_file():
        raise SystemExit(
            f"classification json not found: {source_path} "
            f"(place {_CLASSIFICATION_BASENAME} there or under data/lake/sector/ in the repo)"
        )
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    data = _load_classification_json(source_path)
    major_blueprints, middle_blueprints = _build_group_blueprints(data)
    client = build_kis_client_for_mode(args.mode)
    client.authenticate()
    t_live = time.perf_counter()
    live_signal_map = _build_live_signal_map(client)
    print(f"[timing] live_signal_map  {time.perf_counter() - t_live:.2f}s")

    investor_by_symbol: dict[str, dict[str, Any]] = {}
    t_fi = time.perf_counter()
    try:
        fi_rows = client.fetch_foreign_institution_flow()
        investor_by_symbol = _build_investor_by_symbol(fi_rows)
        if len(fi_rows) == 0:
            print(
                "WARNING: fetch_foreign_institution_flow returned empty rows "
                "(check KIS mode/장 운영/자격증명; 순위 무응답 시 수급 열은 비게 됩니다).",
                file=sys.stderr,
            )
    except Exception as exc:
        print(f"WARNING: fetch_foreign_institution_flow: {exc}", file=sys.stderr)
    print(f"[timing] foreign_institution_flow  {time.perf_counter() - t_fi:.2f}s")

    all_groups = major_blueprints + middle_blueprints
    ew = max(1, int(args.enrichment_workers))
    probe_quotes: dict[str, dict[str, Any]] = {}
    probe_minimal_target_n = 0
    if quote_enrichment and quote_enrichment_mode == "top_by_group":
        probe_syms = _all_unique_stock_symbols_from_groups(all_groups)
        probe_minimal_target_n = len(probe_syms)
        print(
            f"[probe] 1차 최소 시세(현재가·등락·거래대금, 프로그램 제외) — "
            f"{probe_minimal_target_n}종  workers={ew}"
        )
        t_pq = time.perf_counter()
        try:
            probe_quotes, _, _ = _fetch_quote_enrichment_concurrent(
                client,
                probe_syms,
                fetch_investor_per_symbol=False,
                max_workers=ew,
                fetch_program=False,
                log_label="[probe-quote]",
            )
        except Exception as exc:
            print(f"WARNING: probe minimal quotes: {exc}", file=sys.stderr)
            probe_quotes = {}
        print(
            f"[timing] probe_minimal_quotes  {time.perf_counter() - t_pq:.2f}s  "
            f"ok={len(probe_quotes)}/{probe_minimal_target_n}"
        )

    quote_symbols = _select_quote_symbols(
        all_groups,
        live_signal_map,
        top_n=max(1, int(args.top_n)),
        enrichment_mode=quote_enrichment_mode,
        investor_by_symbol=investor_by_symbol,
        program_by_symbol={},
        probe_quotes=probe_quotes if probe_quotes else None,
    )
    print(f"[enrichment] mode={quote_enrichment_mode}  quote_api_targets={len(quote_symbols)}")

    quotes_by_symbol: dict[str, dict[str, Any]] = dict(probe_quotes)
    program_by_symbol: dict[str, dict[str, Any]] = {}
    if quote_enrichment:
        have_rank_investor = bool(investor_by_symbol)
        t_en = time.perf_counter()
        try:
            q2, investor_extra, program_by_symbol = _fetch_quote_enrichment_concurrent(
                client,
                quote_symbols,
                fetch_investor_per_symbol=not have_rank_investor,
                max_workers=ew,
                fetch_program=True,
            )
            quotes_by_symbol.update(q2)
            if not have_rank_investor:
                investor_by_symbol = investor_extra
                if not investor_by_symbol:
                    print(
                        "WARNING: investor-by-symbol fallback also returned empty rows "
                        "(account permission / API availability issue likely).",
                        file=sys.stderr,
                    )
        except Exception as exc:
            print(f"WARNING: quote enrichment: {exc}", file=sys.stderr)
        print(f"[timing] quote_enrichment (incl. per-symbol API)  {time.perf_counter() - t_en:.2f}s")

    t_rows = time.perf_counter()
    major_rows = _build_rows(
        major_blueprints,
        quotes_by_symbol,
        live_signal_map,
        top_n=max(1, int(args.top_n)),
        investor_by_symbol=investor_by_symbol,
        program_by_symbol=program_by_symbol,
    )
    middle_rows = _build_rows(
        middle_blueprints,
        quotes_by_symbol,
        live_signal_map,
        top_n=max(1, int(args.top_n)),
        investor_by_symbol=investor_by_symbol,
        program_by_symbol=program_by_symbol,
    )
    print(f"[timing] build_rows  {time.perf_counter() - t_rows:.2f}s")

    # --- Persistence/Breadth: load history + compute v2 metrics ---
    # Determine "today" date for snapshot key.
    collected_at = max(
        [
            str(m.get("analysis", {}).get("collected_at", ""))
            for m in major_rows + middle_rows
            if isinstance(m, dict)
        ]
        or [""]
    )
    if not collected_at:
        from scripts.collect_sector_data import _now_kst_iso

        collected_at = _now_kst_iso()
    today_yyyy_mm_dd = _parse_iso_date(collected_at) or str(_dt.date.today().isoformat())

    history_dir = Path(args.theme_history_dir)
    history_rows = _load_theme_history_rows(history_dir, max_days=40)

    # market_up_ratio: classification 전체(고유 심볼) 중 당일 상승 비율
    all_unique_syms = _all_unique_stock_symbols_from_groups(major_blueprints + middle_blueprints)
    market_returns: list[float] = []
    for s in all_unique_syms:
        q = quotes_by_symbol.get(_norm_kis_stock_symbol(s)) or {}
        if q.get("return_pct") is None:
            continue
        try:
            market_returns.append(float(q.get("return_pct") or 0.0))
        except Exception:
            continue
    market_up_ratio = (
        (sum(1 for r in market_returns if r > 0.0) / len(market_returns)) if market_returns else None
    )

    # per-symbol value expansion stats from silver snapshot (avg5/avg20)
    value_avgs = _load_symbol_value_averages(all_unique_syms, lookback_days=30)

    def _inject_v2(rows: list[dict[str, Any]], *, cohort_history: list[dict[str, Any]]) -> None:
        for row in rows:
            a = dict(row.get("analysis") or {})
            major = str(row.get("major_category") or "")
            middle = row.get("middle_category")
            key = (major, middle)

            # current snapshot row for persistence inputs
            cur_snap = {
                "date": today_yyyy_mm_dd,
                "group_type": str(row.get("group_type") or ""),
                "major_category": major,
                "middle_category": middle,
                "theme_rs": float(a.get("relative_strength_score") or 0.0),
                "theme_rank": int(a.get("relative_strength_rank") or 0),
                "member_count": int(a.get("member_count") or 0),
                "top_members_value_sum": float(a.get("top_members_value_sum") or 0.0),
                "total_value_traded": float(a.get("total_value_traded") or 0.0),
                "up_ratio": float(a.get("up_ratio") or 0.0),
                "rs60_ratio": float(a.get("rs60_ratio") or 0.0),
                "rs70_ratio": float(a.get("rs70_ratio") or 0.0),
                "leader_status": str(a.get("leader_status") or ""),
            }

            pers = calculate_persistence_score(cur_snap, cohort_history, key)
            breadth_inputs_member_count = int(a.get("member_count") or 0)

            # value_expansion_ratio: 구성 종목 중 (avg5/avg20 >= 1.5) 비중
            expanded_cnt = 0
            denom = 0
            for m in (row.get("major_stocks") or []):
                # NOTE: major_stocks 는 top_n slice 이라 breadth member set으로는 부족.
                # expansion 은 "구성 종목 전체"가 원칙인데, 현재 row에는 전체 members가 없어서
                # 여기서는 최소한 분석 스냅샷(분류 전체) 기반의 평균으로 계산하지 않고,
                # member_count 기준의 대표종목(표시대상) 프록시로 둔다.
                pass
            # 프록시 대신: rs60_ratio/up_ratio 를 all_members 기준으로 이미 계산했으므로,
            # expansion 도 동일하게 all_members 가 필요. 현 구조에서는 members 리스트를 보존하지 않기 때문에,
            # value_expansion_ratio 는 major_stocks(대표) 기반으로 근사한다.
            for m in (row.get("major_stocks") or []):
                sym = _norm_kis_stock_symbol(m.get("symbol", ""))
                if not sym:
                    continue
                av = value_avgs.get(sym)
                if not av:
                    continue
                avg20 = float(av.get("avg20") or 0.0)
                avg5 = float(av.get("avg5") or 0.0)
                if avg20 <= 0:
                    continue
                denom += 1
                if (avg5 / avg20) >= 1.5:
                    expanded_cnt += 1
            value_expansion_ratio = (expanded_cnt / denom) if denom > 0 else None

            br = calculate_breadth_score(
                member_count=breadth_inputs_member_count,
                rs60_ratio=_safe_float(a.get("rs60_ratio"), 0.0),
                rs70_ratio=_safe_float(a.get("rs70_ratio"), 0.0),
                up_ratio=_safe_float(a.get("up_ratio"), 0.0),
                market_up_ratio=market_up_ratio,
                value_expansion_ratio=value_expansion_ratio,
            )

            # merge analysis fields (always present)
            a.update(
                {
                    "persistence_score": pers.get("persistence_score"),
                    "breadth_score": br.get("breadth_score"),
                    "rank_top3_days_5d": pers.get("rank_top3_days_5d", 0),
                    "rank_top5_days_10d": pers.get("rank_top5_days_10d", 0),
                    "rs_avg_5d": pers.get("rs_avg_5d"),
                    "rs_avg_10d": pers.get("rs_avg_10d"),
                    "rs_slope_5d": pers.get("rs_slope_5d"),
                    "value_ratio_5d_20d": pers.get("value_ratio_5d_20d"),
                    "rs60_ratio": br.get("rs60_ratio", a.get("rs60_ratio", 0.0)),
                    "rs70_ratio": br.get("rs70_ratio", a.get("rs70_ratio", 0.0)),
                    "up_ratio": br.get("up_ratio", a.get("up_ratio", 0.0)),
                    "relative_up_ratio": br.get("relative_up_ratio"),
                    "value_expansion_ratio": br.get("value_expansion_ratio"),
                }
            )

            theme_quality = classify_theme_quality(a.get("persistence_score"), a.get("breadth_score"))
            a["theme_quality_label"] = theme_quality

            if a.get("persistence_score") is None or a.get("breadth_score") is None:
                a["theme_score_v2"] = None
            else:
                a["theme_score_v2"] = round(
                    0.60 * float(a.get("relative_strength_score") or 0.0)
                    + 0.20 * float(a.get("persistence_score") or 0.0)
                    + 0.20 * float(a.get("breadth_score") or 0.0),
                    1,
                )

            row["analysis"] = a

        # Sort by v2 when present, fallback to RS
        rows.sort(
            key=lambda r: (
                -float(dict(r.get("analysis") or {}).get("theme_score_v2") or -1.0),
                -float(dict(r.get("analysis") or {}).get("relative_strength_score") or 0.0),
                str(r.get("display_path", "")),
            )
        )

    major_hist = [r for r in history_rows if str(r.get("group_type") or "") == "major"]
    middle_hist = [r for r in history_rows if str(r.get("group_type") or "") == "middle"]
    _inject_v2(major_rows, cohort_history=major_hist)
    _inject_v2(middle_rows, cohort_history=middle_hist)

    # --- Write history snapshot for future runs ---
    snapshot_rows: list[dict[str, Any]] = []
    for row in major_rows + middle_rows:
        a = dict(row.get("analysis") or {})
        snapshot_rows.append(
            {
                "date": today_yyyy_mm_dd,
                "group_type": str(row.get("group_type") or ""),
                "major_category": row.get("major_category"),
                "middle_category": row.get("middle_category"),
                "theme_rs": float(a.get("relative_strength_score") or 0.0),
                "theme_rank": int(a.get("relative_strength_rank") or 0),
                "member_count": int(a.get("member_count") or 0),
                "top_members_value_sum": float(a.get("top_members_value_sum") or 0.0),
                "total_value_traded": float(a.get("total_value_traded") or 0.0),
                "up_ratio": float(a.get("up_ratio") or 0.0),
                "rs60_ratio": float(a.get("rs60_ratio") or 0.0),
                "rs70_ratio": float(a.get("rs70_ratio") or 0.0),
                "leader_status": str(a.get("leader_status") or ""),
            }
        )
    try:
        snap_path = _write_theme_history_snapshot(history_dir, date_yyyy_mm_dd=today_yyyy_mm_dd, rows=snapshot_rows)
        print(f"Wrote theme history snapshot {snap_path}")
    except Exception as exc:
        print(f"WARNING: write theme history snapshot failed: {exc}", file=sys.stderr)
    meta = {
        "schema_version": data.get("schema_version"),
        "major_category_count": data.get("major_category_count"),
        "middle_category_count": data.get("middle_category_count"),
        "unique_stock_count": data.get("unique_stock_count"),
        "live_signal_symbol_count": len(live_signal_map),
        "quote_symbol_count": len(quotes_by_symbol),
        "foreign_institution_rank_symbol_count": len(investor_by_symbol),
        "program_trade_symbol_count": len(program_by_symbol),
        "quote_enrichment_mode": quote_enrichment_mode,
        "quote_enrichment_api_target_count": len(quote_symbols),
        "probe_minimal_quote_target_count": probe_minimal_target_n,
        "probe_minimal_quote_fetched_count": len(probe_quotes),
        "source_file": data.get("source_file"),
        "description": data.get("description"),
        "collected_at": collected_at,
        "theme_history_dir": str(history_dir),
        "theme_snapshot_date": today_yyyy_mm_dd,
        "market_up_ratio": _round1((market_up_ratio or 0.0) * 100.0) if market_up_ratio is not None else None,
    }

    payload = {
        "metadata": meta,
        "major_rows": major_rows,
        "middle_rows": middle_rows,
    }
    json_path = out_dir / "overview.json"
    md_path = out_dir / "overview.md"
    html_path = out_dir / "overview.html"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(
        _render_thema_summary_md(
            source_path=source_path,
            collected_at=collected_at,
            meta=meta,
            major_rows=major_rows,
            middle_rows=middle_rows,
            top_n=max(1, int(args.top_n)),
        ),
        encoding="utf-8",
    )
    html_path.write_text(
        _render_thema_report_html(
            source_path=source_path,
            collected_at=collected_at,
            meta=meta,
            major_rows=major_rows,
            middle_rows=middle_rows,
            top_n=max(1, int(args.top_n)),
        ),
        encoding="utf-8",
    )
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    print(f"Wrote {html_path}")

    if args.telegram:
        bot_token = str(args.telegram_bot_token or "").strip()
        chat_id = str(args.telegram_chat_id or "").strip()
        thread_id = str(args.telegram_thread_id or "").strip() or None
        if not bot_token or not chat_id:
            raise SystemExit(
                "--telegram 사용 시 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID_STOCK 환경변수 또는 "
                "--telegram-bot-token / --telegram-chat-id 인자가 필요합니다."
            )
        try:
            _send_telegram_message(bot_token, chat_id, _telegram_summary(collected_at, major_rows, middle_rows), thread_id=thread_id)
            _send_telegram_document(bot_token, chat_id, html_path, caption=html_path.name, thread_id=thread_id)
            print("Telegram report sent.")
        except Exception as exc:
            print(f"WARNING: Telegram send failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
