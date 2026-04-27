"""
Thema 대분류/중분류 기반 섹터 리포트 생성기.

기존 KIS 업종 리포트(`scripts.collect_sector_data`)와 별개로 실행한다.

예시:
  python -m scripts.collect_thema_sector_data
  python -m scripts.collect_thema_sector_data --mode real --classification-json "C:/Users/me/Downloads/thema_major_middle_stock_classification_dup_allowed.json"
  python -m scripts.collect_thema_sector_data --quote-enrichment
  python -m scripts.collect_thema_sector_data --mode real --telegram

분류 JSON는 `~/Downloads/...` 대신 `data/lake/sector/thema_major_middle_stock_classification_dup_allowed.json` 에 두어도 기본 인자로 인식한다.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
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
from sector_scanner.kis_client import build_kis_client_for_mode


_CLASSIFICATION_BASENAME = "thema_major_middle_stock_classification_dup_allowed.json"
DEFAULT_CLASSIFICATION_JSON = Path.home() / "Downloads" / _CLASSIFICATION_BASENAME
DEFAULT_OUTPUT_DIR = Path("data/lake/sector/thema_major_middle")

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
        "symbol": str(raw.get("stockCode", "")).strip(),
        "name": str(raw.get("stockName", "")).strip(),
        "market_cap_eok": _safe_float(raw.get("marketCap"), 0.0),
        "per_values": per_values,
        "major_category": major,
        "middle_category": middle,
    }


def _dedupe_stocks(stocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for stock in stocks:
        sym = str(stock.get("symbol", "")).strip()
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
        sym = str(rows[0].get("symbol", "")).strip()
        if sym:
            out[sym] = 100.0
        return out
    for idx, row in enumerate(rows):
        sym = str(row.get("symbol", "")).strip()
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
            sym = str(row.get("symbol", "")).strip()
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


def _select_quote_symbols(
    groups: list[dict[str, Any]],
    live_signal_map: dict[str, dict[str, Any]],
    *,
    top_n: int,
    market_cap_fallback: int = 5,
) -> list[str]:
    symbols: set[str] = set(live_signal_map.keys())
    fallback_n = max(int(top_n), int(market_cap_fallback))
    for group in groups:
        stocks = sorted(
            list(group.get("stocks") or []),
            key=lambda x: (
                -float(x.get("market_cap_eok", 0.0)),
                str(x.get("name", "")),
                str(x.get("symbol", "")),
            ),
        )
        for stock in stocks[:fallback_n]:
            sym = str(stock.get("symbol", "")).strip()
            if sym:
                symbols.add(sym)
    return sorted(symbols)


def _fetch_quotes(client, symbols: list[str]) -> dict[str, dict[str, Any]]:
    quotes: dict[str, dict[str, Any]] = {}
    total = len(symbols)
    for idx, sym in enumerate(symbols, start=1):
        try:
            quote = client.fetch_stock_price(sym)
        except Exception:
            quote = {}
        if isinstance(quote, dict) and quote:
            quotes[sym] = quote
        if idx % 25 == 0 or idx == total:
            print(f"[quotes] fetched {idx}/{total}")
    return quotes


def _score_group_members(
    stocks: list[dict[str, Any]],
    quotes_by_symbol: dict[str, dict[str, Any]],
    live_signal_map: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for stock in stocks:
        sym = str(stock.get("symbol", "")).strip()
        quote = dict(quotes_by_symbol.get(sym) or {})
        live = dict(live_signal_map.get(sym) or {})
        rows.append(
            {
                "symbol": sym,
                "name": str(stock.get("name", "")).strip() or sym,
                "market_cap_eok": round(float(stock.get("market_cap_eok", 0.0)), 1),
                "price": quote.get("price"),
                "return_pct": quote.get("return_pct"),
                "value_traded": quote.get("value_traded"),
                "volume": quote.get("volume"),
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
    per_values = [x for stock in list(group.get("stocks") or []) for x in list(stock.get("per_values") or [])]
    avg_per = (sum(per_values) / len(per_values)) if per_values else None
    return {
        "weighted_rs": round(weighted_rs, 2),
        "avg_rs": round(sum(rs_values) / len(rs_values), 2) if rs_values else 0.0,
        "high_rs_ratio": round(high_rs_ratio * 100.0, 1),
        "strong_rs_ratio": round(strong_rs_ratio * 100.0, 1),
        "member_count": member_count,
        "market_cap_total_eok": round(total_cap, 1),
        "market_cap_log": math.log10(total_cap + 1.0),
        "top_members_value_sum": round(top_members_value_sum, 0),
        "top_members_avg_return_pct": round(top_avg_return * 100.0, 2) if top_avg_return is not None else None,
        "top_members_positive_ratio": round(top_positive_ratio * 100.0, 1) if top_return_vals else None,
        "top_member_value_share_pct": round(float(top_value_share or 0.0) * 100.0, 1) if top_value_share is not None else None,
        "quote_coverage_ratio": round((sum(1 for m in members if m.get("return_pct") is not None) / len(members) * 100.0), 1) if members else 0.0,
        "live_signal_coverage_ratio": round((sum(1 for m in members if float(m.get("live_signal_score", 0.0)) > 0.0) / len(members) * 100.0), 1)
        if members
        else 0.0,
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
) -> list[dict[str, Any]]:
    rows = []
    for group in groups:
        all_members = _score_group_members(group["stocks"], quotes_by_symbol, live_signal_map)
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
        if a.get("avg_per") is not None:
            lines.append(f"- PER 평균(단순): `{a.get('avg_per')}`")
        signals = list(a.get("leader_signals") or [])
        if signals:
            lines.append(f"- 주도 신호: {', '.join(signals)}")
        lines.append("")
        lines.append("| 대표 종목 | 코드 | RS | 시총(억) | 당일등락률 | 현재가 | 거래대금(억) |")
        lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: |")
        for m in row.get("major_stocks") or []:
            lines.append(
                f"| {m.get('name', '-')} | `{m.get('symbol', '-')}` | {m.get('rs', '-')} | "
                f"{_fmt_num(m.get('market_cap_eok'))} | {_fmt_pct(m.get('return_pct'))} | {_fmt_num(m.get('price'))} | {_fmt_eok(m.get('value_traded'))} |"
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
        f"> 종목 수: {meta.get('unique_stock_count', '-')} / 시세 보강 종목 수: {meta.get('quote_symbol_count', '-')}",
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
            ("시총가중 RS", a.get("weighted_rs"), None),
            ("평균 RS", a.get("avg_rs"), None),
            ("RS80+ 비중", _fmt_pct(_safe_float(a.get("high_rs_ratio"), 0.0) / 100.0), None),
            ("RS60+ 비중", _fmt_pct(_safe_float(a.get("strong_rs_ratio"), 0.0) / 100.0), None),
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
        parts.append("<thead><tr><th>종목</th><th>코드</th><th>RS</th><th>시총(억)</th><th>당일등락률</th><th>현재가</th><th>거래대금(억)</th></tr></thead><tbody>")
        for m in row.get("major_stocks") or []:
            url = _naver_finance_stock_url(str(m.get("symbol", "")))
            parts.append(
                "<tr>"
                f"<td><a href=\"{_escape_html(url)}\" target=\"_blank\" rel=\"noopener noreferrer\">{_escape_html(m.get('name', '-'))}</a></td>"
                f"<td><a href=\"{_escape_html(url)}\" target=\"_blank\" rel=\"noopener noreferrer\">{_escape_html(m.get('symbol', '-'))}</a></td>"
                f"<td>{_escape_html(m.get('rs', '-'))}</td>"
                f"<td>{_fmt_num(m.get('market_cap_eok'))}</td>"
                f"<td>{_signed_value_html(_fmt_pct(m.get('return_pct')), m.get('return_pct')) if m.get('return_pct') is not None else '-'}</td>"
                f"<td>{_signed_value_html(_fmt_num(m.get('price')), m.get('return_pct')) if m.get('price') is not None else '-'}</td>"
                f"<td>{_fmt_eok(m.get('value_traded'))}</td>"
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
        f"시세보강 {meta.get('quote_symbol_count', '-')} / 대표종목 {top_n}</p>"
        "<div class=\"hero-stats\">"
        f"<div class=\"hero-stat\"><div class=\"label\">대분류</div><div class=\"value\">{len(major_rows)}</div></div>"
        f"<div class=\"hero-stat\"><div class=\"label\">중분류</div><div class=\"value\">{len(middle_rows)}</div></div>"
        f"<div class=\"hero-stat\"><div class=\"label\">주도 그룹</div><div class=\"value\">{leader_count}</div></div>"
        f"<div class=\"hero-stat\"><div class=\"label\">분류 스키마</div><div class=\"value\">{_escape_html(meta.get('schema_version', '-'))}</div></div>"
        "</div></section>"
        f"{_render_leaderboard_html(major_rows, title='대분류 Leaderboard', top_n=top_n, open_by_default=True, footnote='그룹 점수는 다른 대분류와만 비교. 종목 RS·백분위·대표는 해당 대분류(하위 전체, 종목 중복 제거) 구성만 기준.')}"
        f"{_render_leaderboard_html(middle_rows, title='중분류 Leaderboard', top_n=top_n, open_by_default=False, footnote='그룹 점수는 다른 중분류와만 비교(대분류 랭킹과 별도). 종목 RS·백분위·대표는 그 중분류에만 싣은 구성으로만. 표본이 적은 중분류는 지표가 요동칠 수 있음.')}"
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
    parser.add_argument("--top-n", type=int, default=5, help="Top stocks per group.")
    parser.add_argument(
        "--quote-enrichment",
        action="store_true",
        help="KIS 현재가/거래대금 보강 사용. 기본은 분류 JSON만으로 빠르게 생성.",
    )
    parser.add_argument(
        "--no-quote-enrichment",
        dest="quote_enrichment",
        action="store_false",
        help=argparse.SUPPRESS,
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
    parser.set_defaults(quote_enrichment=False)
    args = parser.parse_args()

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
    live_signal_map = _build_live_signal_map(client)
    quote_symbols = _select_quote_symbols(major_blueprints + middle_blueprints, live_signal_map, top_n=max(1, int(args.top_n)))
    quotes_by_symbol: dict[str, dict[str, Any]] = {}
    if args.quote_enrichment:
        quotes_by_symbol = _fetch_quotes(client, quote_symbols)

    major_rows = _build_rows(major_blueprints, quotes_by_symbol, live_signal_map, top_n=max(1, int(args.top_n)))
    middle_rows = _build_rows(middle_blueprints, quotes_by_symbol, live_signal_map, top_n=max(1, int(args.top_n)))
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
    meta = {
        "schema_version": data.get("schema_version"),
        "major_category_count": data.get("major_category_count"),
        "middle_category_count": data.get("middle_category_count"),
        "unique_stock_count": data.get("unique_stock_count"),
        "live_signal_symbol_count": len(live_signal_map),
        "quote_symbol_count": len(quotes_by_symbol),
        "source_file": data.get("source_file"),
        "description": data.get("description"),
        "collected_at": collected_at,
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
