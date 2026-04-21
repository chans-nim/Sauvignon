"""
업종 마스터(idxcode) + (선택) KIS 업종 지수 + 업종별 구성 종목(거래량 순위) 수집.

  # idxcode.mst 만 (KRX, 네트워크)
  python -m scripts.collect_sector_data --idxcode-only

  # KIS 실계정: 업종 스냅샷 + 특정 업종 종목
  python -m scripts.collect_sector_data --mode real --sector 0002 --members

환경 변수: KIS_APP_KEY, KIS_APP_SECRET (paper 모드 시 KIS_PAPER_*)
"""

from __future__ import annotations

import argparse
import html
import json
import mimetypes
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import parse, request
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional fallback
    load_dotenv = None

if __name__ == "__main__" and str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if load_dotenv is not None:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _sector_snapshot_map(sectors: list[Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for s in sectors:
        try:
            d = s.to_dict()
        except AttributeError:
            if isinstance(s, dict):
                d = dict(s)
            else:
                continue
        code = str(d.get("sector_code", "")).strip()
        if code:
            out[code] = d
    return out


def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(str(x).replace(",", ""))
    except Exception:
        return default


def _now_kst_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Seoul")).isoformat()


def _parse_iso_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _fmt_ts(value: Any) -> str:
    dt = _parse_iso_dt(value)
    if dt is None:
        return str(value or "-")
    return dt.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S %Z")


def _pct_rank_map(values: list[float], *, reverse: bool = True) -> dict[float, float]:
    uniq = sorted({float(v) for v in values}, reverse=reverse)
    if not uniq:
        return {}
    if len(uniq) == 1:
        return {uniq[0]: 100.0}
    denom = float(len(uniq) - 1)
    out: dict[float, float] = {}
    for idx, val in enumerate(uniq):
        score = 100.0 * (len(uniq) - 1 - idx) / denom if reverse else 100.0 * idx / denom
        out[val] = score
    return out


def _stock_stats(members: list[dict[str, Any]]) -> dict[str, float | None]:
    returns: list[float] = []
    value_traded: list[float] = []
    for member in members:
        ret = _stock_return_pct(member)
        if ret is not None:
            returns.append(ret)
        vt = _safe_float(member.get("value_traded"), 0.0)
        if vt > 0:
            value_traded.append(vt)
    avg_ret = sum(returns) / len(returns) if returns else None
    positive_ratio = (sum(1 for x in returns if x > 0) / len(returns)) if returns else None
    total_vt = sum(value_traded)
    top_share = (max(value_traded) / total_vt) if total_vt > 0 else None
    return {
        "member_avg_return_pct": avg_ret,
        "member_positive_ratio": positive_ratio,
        "top_member_share": top_share,
        "member_count_with_return": float(len(returns)) if returns else 0.0,
    }


def _intraday_range_stats(snapshot: dict[str, Any]) -> dict[str, float | None]:
    current = _safe_float(snapshot.get("current_index"), 0.0)
    high = _safe_float(snapshot.get("high_index"), 0.0)
    low = _safe_float(snapshot.get("low_index"), 0.0)
    if current <= 0 or high <= 0 or high <= low:
        return {"range_position_pct": None, "distance_from_high_pct": None}
    range_position = max(0.0, min(1.0, (current - low) / (high - low)))
    distance_from_high = max(0.0, (high - current) / high)
    return {
        "range_position_pct": range_position,
        "distance_from_high_pct": distance_from_high,
    }


def _collection_window_text(rows: list[dict[str, Any]]) -> str:
    dts = []
    for row in rows:
        snap = dict(row.get("snapshot") or {})
        dt = _parse_iso_dt(snap.get("as_of"))
        if dt is not None:
            dts.append(dt)
    if not dts:
        return "-"
    start = min(dts)
    end = max(dts)
    start_text = _fmt_ts(start.isoformat())
    end_text = _fmt_ts(end.isoformat())
    if start == end:
        return start_text
    return f"{start_text} ~ {end_text}"


def _analyze_sector_overview(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return rows
    metrics: list[dict[str, float | str | None]] = []
    for row in rows:
        snap = dict(row.get("snapshot") or {})
        stock_stats = _stock_stats(list(row.get("major_stocks") or []))
        range_stats = _intraday_range_stats(snap)
        flow_support = _safe_float(snap.get("program_flow_strength"), 0.0) + _safe_float(
            snap.get("foreign_institution_flow_strength"), 0.0
        )
        collected_at = str(snap.get("as_of") or "").strip() or _now_kst_iso()
        if not str(snap.get("as_of") or "").strip():
            snap["as_of"] = collected_at
            row["snapshot"] = snap
        metrics.append(
            {
                "return_pct": _safe_float(snap.get("return_pct"), 0.0),
                "intraday_trend": _safe_float(snap.get("intraday_trend"), 0.0),
                "acceleration": _safe_float(snap.get("acceleration"), 0.0),
                "range_position_pct": range_stats["range_position_pct"] if range_stats["range_position_pct"] is not None else 0.0,
                "distance_from_high_pct": range_stats["distance_from_high_pct"],
                "member_avg_return_pct": stock_stats["member_avg_return_pct"] if stock_stats["member_avg_return_pct"] is not None else 0.0,
                "member_positive_ratio": stock_stats["member_positive_ratio"] if stock_stats["member_positive_ratio"] is not None else 0.0,
                "top_member_share": stock_stats["top_member_share"],
                "flow_support_strength": flow_support,
                "collected_at": collected_at,
            }
        )

    keys = (
        "return_pct",
        "intraday_trend",
        "acceleration",
        "range_position_pct",
        "member_avg_return_pct",
        "member_positive_ratio",
    )
    pct_maps = {k: _pct_rank_map([float(m[k] or 0.0) for m in metrics]) for k in keys}
    strength_scores: list[float] = []
    for metric in metrics:
        rs = (
            pct_maps["return_pct"].get(float(metric["return_pct"] or 0.0), 0.0) * 0.30
            + pct_maps["intraday_trend"].get(float(metric["intraday_trend"] or 0.0), 0.0) * 0.15
            + pct_maps["acceleration"].get(float(metric["acceleration"] or 0.0), 0.0) * 0.10
            + pct_maps["range_position_pct"].get(float(metric["range_position_pct"] or 0.0), 0.0) * 0.10
            + pct_maps["member_avg_return_pct"].get(float(metric["member_avg_return_pct"] or 0.0), 0.0) * 0.20
            + pct_maps["member_positive_ratio"].get(float(metric["member_positive_ratio"] or 0.0), 0.0) * 0.15
        )
        strength_scores.append(rs)

    score_order = sorted(strength_scores, reverse=True)
    total = len(rows)
    for idx, row in enumerate(rows):
        snap = dict(row.get("snapshot") or {})
        metric = metrics[idx]
        rs_score = round(strength_scores[idx], 1)
        rs_rank = score_order.index(strength_scores[idx]) + 1
        signal_labels: list[str] = []
        if float(metric["return_pct"] or 0.0) > 0:
            signal_labels.append("섹터 수익률 플러스")
        if float(metric["intraday_trend"] or 0.0) > 0:
            signal_labels.append("장중 추세 상방")
        if float(metric["acceleration"] or 0.0) > 0:
            signal_labels.append("상승 가속")
        if float(metric["member_positive_ratio"] or 0.0) >= 0.6:
            signal_labels.append("상위 종목 상승 종목 비중 높음")
        if float(metric["member_avg_return_pct"] or 0.0) > 0:
            signal_labels.append("상위 종목 평균 수익률 플러스")
        if float(metric["range_position_pct"] or 0.0) >= 0.7:
            signal_labels.append("장중 고가권 유지")
        top_share = metric["top_member_share"]
        if top_share is not None and float(top_share) <= 0.65:
            signal_labels.append("거래대금 쏠림 완화")

        if rs_score >= 75.0 and len(signal_labels) >= 4 and float(metric["member_positive_ratio"] or 0.0) >= 0.6:
            leader_status = "주도"
        elif rs_score >= 55.0 and len(signal_labels) >= 3:
            leader_status = "관심"
        elif rs_score >= 40.0:
            leader_status = "중립"
        else:
            leader_status = "약세"

        row["analysis"] = {
            "collected_at": metric["collected_at"],
            "relative_strength_score": rs_score,
            "relative_strength_rank": rs_rank,
            "relative_strength_total": total,
            "leader_status": leader_status,
            "leader_candidate": leader_status == "주도",
            "leader_signal_count": len(signal_labels),
            "leader_signals": signal_labels,
            "range_position_pct": round(float(metric["range_position_pct"] or 0.0) * 100.0, 1)
            if metric["range_position_pct"] is not None
            else None,
            "distance_from_high_pct": round(float(metric["distance_from_high_pct"] or 0.0) * 100.0, 2)
            if metric["distance_from_high_pct"] is not None
            else None,
            "member_avg_return_pct": round(float(metric["member_avg_return_pct"] or 0.0) * 100.0, 2)
            if metric["member_avg_return_pct"] is not None
            else None,
            "member_positive_ratio": round(float(metric["member_positive_ratio"] or 0.0) * 100.0, 1)
            if metric["member_positive_ratio"] is not None
            else None,
            "top_member_share_pct": round(float(top_share or 0.0) * 100.0, 1) if top_share is not None else None,
            "flow_support_strength": round(float(metric["flow_support_strength"] or 0.0), 4),
            "component_percentiles": {
                "sector_return_pct": round(pct_maps["return_pct"].get(float(metric["return_pct"] or 0.0), 0.0), 1),
                "intraday_trend": round(pct_maps["intraday_trend"].get(float(metric["intraday_trend"] or 0.0), 0.0), 1),
                "acceleration": round(pct_maps["acceleration"].get(float(metric["acceleration"] or 0.0), 0.0), 1),
                "range_position": round(pct_maps["range_position_pct"].get(float(metric["range_position_pct"] or 0.0), 0.0), 1),
                "member_avg_return": round(
                    pct_maps["member_avg_return_pct"].get(float(metric["member_avg_return_pct"] or 0.0), 0.0), 1
                ),
                "member_positive_ratio": round(
                    pct_maps["member_positive_ratio"].get(float(metric["member_positive_ratio"] or 0.0), 0.0), 1
                ),
            },
        }
        snap["analysis_summary"] = {
            "relative_strength_score": rs_score,
            "leader_status": leader_status,
            "collected_at": metric["collected_at"],
        }
        row["snapshot"] = snap

    rows.sort(
        key=lambda x: (
            -float(dict(x.get("analysis") or {}).get("relative_strength_score", 0.0)),
            str(x.get("sector_name", "")),
            str(x.get("sector_code", "")),
        )
    )
    return rows


def _augment_snapshot_with_code(
    snapshot: dict[str, Any] | None,
    *,
    sector_code_full: str,
    api_sector_code: str,
    sector_name: str,
    collected_at: str | None = None,
) -> dict[str, Any]:
    d = dict(snapshot or {})
    d.setdefault("sector_code", sector_code_full)
    d.setdefault("api_sector_code", api_sector_code)
    d.setdefault("sector_name", sector_name or sector_code_full)
    if collected_at:
        d.setdefault("as_of", collected_at)
    return d


def _sector_seed_rows(idx_rows: list[dict[str, Any]], sectors: list[Any]) -> list[dict[str, Any]]:
    """
    업종 seed는 idxcode를 기본으로 유지한다.
    KIS 실시간 응답은 전체 업종이 아니라 종합/코스닥(0001/1001)만 내려오는 경우가 있어
    그것만으로 seed를 대체하면 실제 업종코드가 사라진다.

    따라서:
    - idxcode rows를 기본 seed로 사용
    - KIS 실시간 응답의 추가 코드가 있으면 뒤에 보강
    """
    live_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for s in sectors:
        try:
            d = s.to_dict()
        except AttributeError:
            if isinstance(s, dict):
                d = dict(s)
            else:
                continue
        code = str(d.get("sector_code", "")).strip()
        name = str(d.get("sector_name", "")).strip()
        if not code or code in seen:
            continue
        seen.add(code)
        live_rows.append(
            {
                "sector_code_4": code[-4:] if len(code) >= 4 else code,
                "sector_name": name or code,
                "idx_div": "",
                "sector_code_full": code,
            }
        )
    merged: list[dict[str, Any]] = []
    used: set[str] = set()
    for row in idx_rows:
        code_full = str(row.get("sector_code_full", "")).strip() or str(row.get("sector_code_4", "")).strip()
        if not code_full or code_full in used:
            continue
        used.add(code_full)
        merged.append(dict(row))
    for row in live_rows:
        code_full = str(row.get("sector_code_full", "")).strip() or str(row.get("sector_code_4", "")).strip()
        if not code_full or code_full in used:
            continue
        used.add(code_full)
        merged.append(dict(row))
    return merged


def _render_sector_summary_md(rows: list[dict[str, Any]], *, top_n: int) -> str:
    lines: list[str] = []
    lines.append("# Sector Overview")
    lines.append("")
    lines.append(f"> 생성 시각: {_collection_window_text(rows)}")
    lines.append(f"> 섹터 수: {len(rows)} / 섹터별 대표 종목 수: {top_n}")
    lines.append("> 상대강도 기준: sector return + intraday trend + acceleration + top-stock breadth")
    lines.append("")
    lines.append("## Top Leaderboard")
    lines.append("")
    lines.append("| 순위 | 섹터 | RS | 상태 | 수집 시각 |")
    lines.append("| --- | --- | ---: | --- | --- |")
    for idx, row in enumerate(rows[:10], start=1):
        analysis = dict(row.get("analysis") or {})
        code = str(row.get("sector_code", "")).strip()
        api_code = str(row.get("api_sector_code", "")).strip()
        name = str(row.get("sector_name", "")).strip() or code
        display = f"{name} `{code}`"
        if api_code and api_code != code:
            display = f"{name} `{code}->{api_code}`"
        lines.append(
            f"| {idx} | {display} | {analysis.get('relative_strength_score', '-')} | "
            f"{analysis.get('leader_status', '-')} | {_fmt_ts(analysis.get('collected_at'))} |"
        )
    lines.append("")
    for row in rows:
        code = str(row.get("sector_code", "")).strip()
        name = str(row.get("sector_name", "")).strip() or code
        members = row.get("major_stocks") or []
        snap = row.get("snapshot") or {}
        analysis = dict(row.get("analysis") or {})
        ret = snap.get("return_pct")
        trend = snap.get("intraday_trend")
        accel = snap.get("acceleration")
        api_code = str(row.get("api_sector_code", "")).strip()
        rank = analysis.get("relative_strength_rank", "-")
        total = analysis.get("relative_strength_total", "-")
        display = f"{name} (`{code}`)"
        if api_code and api_code != code:
            display = f"{name} (`{code}` -> `{api_code}`)"
        lines.append(f"## {rank}. {display}")
        lines.append("")
        lines.append(
            f"> 수집 시각: {_fmt_ts(analysis.get('collected_at') or snap.get('as_of'))}  "
            f"|  RS: **{analysis.get('relative_strength_score', '-')}** ({rank}/{total})  "
            f"|  상태: **{analysis.get('leader_status', '-')}**"
        )
        lines.append("")
        lines.append(
            f"- 섹터 모멘텀: 등락률 `{_fmt_pct(ret)}` / 장중 추세 `{_fmt_pct(trend)}` / "
            f"가속도 `{_fmt_pct(accel)}`"
        )
        lines.append(
            f"- 주도 체크: 고가권 `{_fmt_pct(_safe_float(analysis.get('range_position_pct'), 0.0) / 100.0) if analysis.get('range_position_pct') is not None else '-'}` / "
            f"고가 이격 `{_fmt_pct(_safe_float(analysis.get('distance_from_high_pct'), 0.0) / 100.0) if analysis.get('distance_from_high_pct') is not None else '-'}` / "
            f"상위 종목 평균 `{_fmt_pct(_safe_float(analysis.get('member_avg_return_pct'), 0.0) / 100.0) if analysis.get('member_avg_return_pct') is not None else '-'}` / "
            f"상승 비중 `{_fmt_pct(_safe_float(analysis.get('member_positive_ratio'), 0.0) / 100.0) if analysis.get('member_positive_ratio') is not None else '-'}`"
        )
        lines.append(
            f"- 거래대금 쏠림: 상위 1종목 비중 `{_fmt_pct(_safe_float(analysis.get('top_member_share_pct'), 0.0) / 100.0) if analysis.get('top_member_share_pct') is not None else '-'}` / "
            f"수급 보강 `{_fmt_num(analysis.get('flow_support_strength'))}` / "
            f"신호 개수 `{analysis.get('leader_signal_count', 0)}`"
        )
        signals = list(analysis.get("leader_signals") or [])
        if signals:
            lines.append(f"- 주도 신호: {', '.join(signals)}")
        if not members:
            lines.append("- 주요 종목 없음")
            lines.append("")
            continue
        lines.append("")
        lines.append("| 대표 종목 | 코드 | 등락률 | 현재가 | 거래대금(억) | 순위 |")
        lines.append("| --- | --- | ---: | ---: | ---: | ---: |")
        for m in members:
            sym = str(m.get("symbol", "")).strip()
            nm = str(m.get("name", "")).strip() or sym
            rk = m.get("rank")
            vt = m.get("value_traded")
            lines.append(
                f"| {nm} | `{sym}` | {_fmt_pct(_stock_return_pct(m))} | {_fmt_num(_stock_price(m))} | "
                f"{_fmt_eok(vt)} | {rk if rk not in (None, '') else '-'} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _market_label_for_code(code_full: str) -> str:
    code = str(code_full).strip()
    if code.startswith("0"):
        return "KOSPI"
    if code.startswith("1"):
        return "KOSDAQ"
    return "OTHER"


def _fmt_num(x: Any) -> str:
    if x in (None, ""):
        return "-"
    try:
        v = float(x)
    except Exception:
        return str(x)
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    return f"{v:,.2f}"


def _fmt_eok(x: Any) -> str:
    if x in (None, ""):
        return "-"
    try:
        v = float(x)
    except Exception:
        return str(x)
    return f"{v / 100_000_000:,.1f}"


def _fmt_pct(x: Any) -> str:
    if x in (None, ""):
        return "-"
    try:
        v = float(x)
    except Exception:
        return str(x)
    return f"{v * 100:.2f}%"


def _stock_return_pct(member: dict[str, Any]) -> float | None:
    raw = dict(member.get("raw") or {})
    v = raw.get("prdy_ctrt")
    if v in (None, ""):
        return None
    try:
        return float(v) / 100.0
    except Exception:
        return None


def _stock_price(member: dict[str, Any]) -> float | None:
    raw = dict(member.get("raw") or {})
    v = raw.get("stck_prpr")
    if v in (None, ""):
        return None
    try:
        return float(v)
    except Exception:
        return None


def _escape_html(value: Any) -> str:
    return html.escape(str(value))


def _naver_finance_stock_url(symbol: str) -> str:
    code = str(symbol or "").strip()
    return f"https://finance.naver.com/item/main.naver?code={parse.quote(code)}"


def _build_telegram_summary(rows: list[dict[str, Any]], *, top_k: int = 5) -> str:
    lines = [
        "섹터 리포트 생성 완료",
        f"- 수집 시각: {_collection_window_text(rows)}",
        f"- 대상 섹터 수: {len(rows)}",
        "- 상위 섹터:",
    ]
    for row in rows[:top_k]:
        analysis = dict(row.get("analysis") or {})
        name = str(row.get("sector_name", "")).strip() or str(row.get("sector_code", "")).strip()
        lines.append(
            f"  · {name}: RS {analysis.get('relative_strength_score', '-')} / "
            f"{analysis.get('leader_status', '-')}"
        )
    return "\n".join(lines)


def _http_post(url: str, data: bytes, headers: dict[str, str]) -> dict[str, Any]:
    req = request.Request(url, data=data, headers=headers, method="POST")
    with request.urlopen(req, timeout=60) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body)


def _telegram_api_url(bot_token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{bot_token}/{method}"


def _send_telegram_message(bot_token: str, chat_id: str, text: str, *, thread_id: str | None = None) -> None:
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if thread_id:
        payload["message_thread_id"] = thread_id
    data = parse.urlencode(payload).encode("utf-8")
    res = _http_post(
        _telegram_api_url(bot_token, "sendMessage"),
        data,
        {"Content-Type": "application/x-www-form-urlencoded"},
    )
    if not res.get("ok"):
        raise RuntimeError(f"Telegram sendMessage failed: {res}")


def _send_telegram_document(
    bot_token: str,
    chat_id: str,
    file_path: Path,
    *,
    caption: str = "",
    thread_id: str | None = None,
) -> None:
    boundary = f"----CursorTelegram{uuid.uuid4().hex}"
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    parts: list[bytes] = []

    def _field(name: str, value: str) -> None:
        parts.append(f"--{boundary}\r\n".encode("utf-8"))
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        parts.append(value.encode("utf-8"))
        parts.append(b"\r\n")

    _field("chat_id", chat_id)
    if caption:
        _field("caption", caption[:1024])
    if thread_id:
        _field("message_thread_id", thread_id)
    parts.append(f"--{boundary}\r\n".encode("utf-8"))
    parts.append(
        (
            f'Content-Disposition: form-data; name="document"; filename="{file_path.name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(file_path.read_bytes())
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    res = _http_post(
        _telegram_api_url(bot_token, "sendDocument"),
        body,
        {"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    if not res.get("ok"):
        raise RuntimeError(f"Telegram sendDocument failed: {res}")


def _send_telegram_report_bundle(
    rows: list[dict[str, Any]],
    *,
    html_path: Path,
    bot_token: str,
    chat_id: str,
    thread_id: str | None = None,
) -> None:
    _send_telegram_message(bot_token, chat_id, _build_telegram_summary(rows), thread_id=thread_id)
    _send_telegram_document(
        bot_token,
        chat_id,
        html_path,
        caption="sector_overview.html",
        thread_id=thread_id,
    )


def _render_sector_report_html(rows: list[dict[str, Any]], *, top_n: int) -> str:
    parts: list[str] = []
    parts.append("<!doctype html>")
    parts.append("<html lang=\"ko\">")
    parts.append("<head>")
    parts.append("<meta charset=\"utf-8\">")
    parts.append("<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">")
    parts.append("<title>Sector Overview</title>")
    parts.append(
        "<style>"
        ":root{--bg:#0b1020;--panel:#ffffff;--panel-soft:#f4f7fb;--text:#111827;--muted:#667085;--line:#e5e7eb;"
        "--up:#0f9f6e;--down:#d92d20;--flat:#667085;--leader:#0ea5e9;--watch:#f59e0b;}"
        "body{font-family:Segoe UI,Arial,sans-serif;margin:0;background:linear-gradient(180deg,#0b1020 0,#10182d 280px,#eef3f8 280px,#eef3f8 100%);color:var(--text);}"
        ".wrap{max-width:1440px;margin:0 auto;padding:28px 24px 48px;}"
        ".hero{background:linear-gradient(135deg,#0f172a,#1d4ed8 60%,#0ea5e9);color:#fff;border-radius:24px;padding:28px 32px;box-shadow:0 20px 40px rgba(15,23,42,.22);}"
        ".hero h1{margin:0 0 10px;font-size:32px;}.hero p{margin:0;color:rgba(255,255,255,.86);font-size:14px;}"
        ".hero-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-top:18px;}"
        ".hero-stat{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.18);border-radius:16px;padding:14px 16px;backdrop-filter:blur(4px);}"
        ".hero-stat .label{font-size:12px;color:rgba(255,255,255,.75);}.hero-stat .value{margin-top:6px;font-size:20px;font-weight:700;}"
        ".section{margin-top:24px;}.section-title{margin:0 0 12px;font-size:18px;color:#0f172a;}"
        ".leaderboard,.card{background:var(--panel);border:1px solid rgba(15,23,42,.06);border-radius:22px;box-shadow:0 14px 34px rgba(15,23,42,.08);}"
        ".leaderboard{padding:18px 20px;}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(430px,1fr));gap:18px;}"
        ".card{padding:18px;position:relative;overflow:hidden;}"
        ".card::before{content:'';position:absolute;inset:0 auto auto 0;width:100%;height:4px;background:linear-gradient(90deg,#38bdf8,#2563eb);opacity:.9;}"
        ".card-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;margin-bottom:14px;}"
        ".card h2{margin:0;font-size:22px;}.sub{margin-top:4px;color:var(--muted);font-size:12px;}"
        ".meta,.metrics,.signal-list{display:flex;flex-wrap:wrap;gap:8px;}"
        ".pill{font-size:12px;padding:6px 10px;border-radius:999px;background:#f3f6fb;border:1px solid #dbe3ef;color:#334155;}"
        ".leader-yes{background:#dcfce7;border-color:#86efac;color:#166534;}.leader-watch{background:#fef3c7;border-color:#fcd34d;color:#92400e;}.leader-flat{background:#eef2f7;border-color:#d0d7e2;color:#475467;}"
        ".rs-box{text-align:right;min-width:110px;}.rs-score{font-size:28px;font-weight:800;color:#0f172a;line-height:1;}.rs-rank{margin-top:6px;color:var(--muted);font-size:12px;}"
        ".rs-bar{margin-top:10px;height:8px;border-radius:999px;background:#e6edf7;overflow:hidden;}.rs-fill{height:100%;background:linear-gradient(90deg,#38bdf8,#2563eb);border-radius:999px;}"
        ".metrics{margin:14px 0 12px;}.metric{flex:1 1 160px;background:var(--panel-soft);border:1px solid var(--line);border-radius:16px;padding:12px;}"
        ".metric .k{font-size:12px;color:var(--muted);}.metric .v{margin-top:6px;font-size:18px;font-weight:700;}"
        ".signal-list{margin-top:10px;}.signal{font-size:12px;padding:6px 10px;border-radius:999px;background:#eff6ff;border:1px solid #bfdbfe;color:#1d4ed8;}"
        "table{width:100%;border-collapse:separate;border-spacing:0;font-size:13px;margin-top:14px;overflow:hidden;border:1px solid var(--line);border-radius:16px;}"
        "th,td{padding:10px 10px;border-top:1px solid var(--line);text-align:right;background:#fff;}"
        "thead th{background:#f8fafc;border-top:none;color:#334155;font-weight:700;}"
        "tbody tr:nth-child(even) td{background:#fbfdff;}"
        "th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left;}"
        ".up{color:var(--up);font-weight:700;}.down{color:var(--down);font-weight:700;}.flat{color:var(--flat);}"
        ".muted{color:var(--muted);font-size:13px;}.leaderboard-table th,.leaderboard-table td{text-align:left;}"
        ".leaderboard-table th:nth-child(3),.leaderboard-table td:nth-child(3),.leaderboard-table th:nth-child(4),.leaderboard-table td:nth-child(4){text-align:right;}"
        "@media (max-width:720px){.wrap{padding:18px 14px 36px;}.hero{padding:22px 20px;}.card-head{flex-direction:column;}.rs-box{text-align:left;}}"
        "</style>"
    )
    parts.append("</head><body>")
    parts.append("<div class=\"wrap\">")
    parts.append("<section class=\"hero\">")
    parts.append("<h1>Sector Overview</h1>")
    parts.append(
        "<p>"
        f"수집 시각: {_escape_html(_collection_window_text(rows))} / "
        "상대강도 기준: sector return + intraday trend + acceleration + top-stock breadth"
        "</p>"
    )
    leader_count = sum(1 for row in rows if dict(row.get("analysis") or {}).get("leader_status") == "주도")
    watch_count = sum(1 for row in rows if dict(row.get("analysis") or {}).get("leader_status") == "관심")
    parts.append("<div class=\"hero-stats\">")
    parts.append(f"<div class=\"hero-stat\"><div class=\"label\">섹터 수</div><div class=\"value\">{len(rows)}</div></div>")
    parts.append(f"<div class=\"hero-stat\"><div class=\"label\">주도 섹터</div><div class=\"value\">{leader_count}</div></div>")
    parts.append(f"<div class=\"hero-stat\"><div class=\"label\">관심 섹터</div><div class=\"value\">{watch_count}</div></div>")
    parts.append(f"<div class=\"hero-stat\"><div class=\"label\">대표 종목 수</div><div class=\"value\">{top_n}</div></div>")
    parts.append("</div>")
    parts.append("</section>")
    parts.append("<section class=\"section\">")
    parts.append("<h2 class=\"section-title\">Top Leaderboard</h2>")
    parts.append("<div class=\"leaderboard\">")
    parts.append("<table class=\"leaderboard-table\"><thead><tr><th>순위</th><th>섹터</th><th>RS</th><th>상태</th><th>수집 시각</th></tr></thead><tbody>")
    for idx, row in enumerate(rows[:10], start=1):
        analysis = dict(row.get("analysis") or {})
        code = str(row.get("sector_code", "")).strip()
        api_code = str(row.get("api_sector_code", "")).strip()
        name = str(row.get("sector_name", "")).strip() or code
        display = f"{_escape_html(name)} <span class=\"muted\">{_escape_html(code if not api_code or api_code == code else f'{code}->{api_code}')}</span>"
        parts.append(
            "<tr>"
            f"<td>{idx}</td>"
            f"<td>{display}</td>"
            f"<td>{analysis.get('relative_strength_score', '-')}</td>"
            f"<td>{_escape_html(analysis.get('leader_status', '-'))}</td>"
            f"<td>{_escape_html(_fmt_ts(analysis.get('collected_at')))}</td>"
            "</tr>"
        )
    parts.append("</tbody></table>")
    parts.append("</div>")
    parts.append("</section>")
    parts.append("<section class=\"section\">")
    parts.append("<h2 class=\"section-title\">Sector Cards</h2>")
    parts.append("<div class=\"grid\">")
    for row in rows:
        code = str(row.get("sector_code", "")).strip()
        api_code = str(row.get("api_sector_code", "")).strip()
        name = str(row.get("sector_name", "")).strip() or code
        market = str(row.get("market_label", "")).strip() or _market_label_for_code(code)
        snap = dict(row.get("snapshot") or {})
        analysis = dict(row.get("analysis") or {})
        members = list(row.get("major_stocks") or [])
        leader_cls = "leader-flat"
        if analysis.get("leader_status") == "주도":
            leader_cls = "leader-yes"
        elif analysis.get("leader_status") == "관심":
            leader_cls = "leader-watch"
        parts.append("<section class=\"card\">")
        parts.append("<div class=\"card-head\">")
        parts.append("<div>")
        parts.append(f"<h2>{_escape_html(name)}</h2>")
        parts.append(
            f"<div class=\"sub\">{_escape_html(market)} / "
            f"sector_code={_escape_html(code)} / api_code={_escape_html(api_code or '-')} / "
            f"수집={_escape_html(_fmt_ts(analysis.get('collected_at') or snap.get('as_of')))}</div>"
        )
        parts.append("<div class=\"meta\">")
        parts.append(f"<span class=\"pill {leader_cls}\">{_escape_html(analysis.get('leader_status', '-'))}</span>")
        parts.append(f"<span class=\"pill\">signals {analysis.get('leader_signal_count', 0)}</span>")
        parts.append(f"<span class=\"pill\">current_index {_escape_html(_fmt_num(snap.get('current_index')))}</span>")
        parts.append(f"<span class=\"pill\">고가 {_escape_html(_fmt_num(snap.get('high_index')))}</span>")
        parts.append(f"<span class=\"pill\">저가 {_escape_html(_fmt_num(snap.get('low_index')))}</span>")
        parts.append("</div>")
        parts.append("</div>")
        rs_value = float(_safe_float(analysis.get("relative_strength_score"), 0.0))
        parts.append("<div class=\"rs-box\">")
        parts.append(f"<div class=\"rs-score\">{rs_value:.1f}</div>")
        parts.append(
            f"<div class=\"rs-rank\">RS Rank #{analysis.get('relative_strength_rank', '-')} / "
            f"{analysis.get('relative_strength_total', '-')}</div>"
        )
        parts.append(f"<div class=\"rs-bar\"><div class=\"rs-fill\" style=\"width:{max(0.0, min(100.0, rs_value)):.1f}%\"></div></div>")
        parts.append("</div>")
        parts.append("</div>")
        parts.append("<div class=\"metrics\">")
        metric_pairs = [
            ("등락률", _fmt_pct(snap.get("return_pct"))),
            ("장중 추세", _fmt_pct(snap.get("intraday_trend"))),
            ("가속도", _fmt_pct(snap.get("acceleration"))),
            (
                "상위 종목 평균",
                _fmt_pct(_safe_float(analysis.get("member_avg_return_pct"), 0.0) / 100.0)
                if analysis.get("member_avg_return_pct") is not None
                else "-",
            ),
            (
                "상승 비중",
                _fmt_pct(_safe_float(analysis.get("member_positive_ratio"), 0.0) / 100.0)
                if analysis.get("member_positive_ratio") is not None
                else "-",
            ),
            (
                "고가권 위치",
                _fmt_pct(_safe_float(analysis.get("range_position_pct"), 0.0) / 100.0)
                if analysis.get("range_position_pct") is not None
                else "-",
            ),
            (
                "고가 이격",
                _fmt_pct(_safe_float(analysis.get("distance_from_high_pct"), 0.0) / 100.0)
                if analysis.get("distance_from_high_pct") is not None
                else "-",
            ),
            (
                "상위1 비중",
                _fmt_pct(_safe_float(analysis.get("top_member_share_pct"), 0.0) / 100.0)
                if analysis.get("top_member_share_pct") is not None
                else "-",
            ),
        ]
        for label, value in metric_pairs:
            parts.append(
                f"<div class=\"metric\"><div class=\"k\">{_escape_html(label)}</div><div class=\"v\">{_escape_html(value)}</div></div>"
            )
        parts.append("</div>")
        signals = list(analysis.get("leader_signals") or [])
        if signals:
            parts.append("<div class=\"signal-list\">")
            for signal in signals:
                parts.append(f"<span class=\"signal\">{_escape_html(signal)}</span>")
            parts.append("</div>")
        if not members:
            parts.append("<p class=\"muted\">주요 종목 없음</p>")
            parts.append("</section>")
            continue
        parts.append("<table>")
        parts.append("<thead><tr><th>종목</th><th>코드</th><th>등락률</th><th>현재가</th><th>거래대금(억)</th><th>거래량</th><th>순위</th></tr></thead><tbody>")
        for m in members:
            sym = str(m.get("symbol", "")).strip()
            nm = str(m.get("name", "")).strip() or sym
            ret = _stock_return_pct(m)
            px = _stock_price(m)
            vt = m.get("value_traded")
            vol = m.get("volume")
            rk = m.get("rank")
            cls = "flat"
            if ret is not None:
                if ret > 0:
                    cls = "up"
                elif ret < 0:
                    cls = "down"
            parts.append(
                "<tr>"
                f"<td><a href=\"{_escape_html(_naver_finance_stock_url(sym))}\" target=\"_blank\" rel=\"noopener noreferrer\">{_escape_html(nm)}</a></td>"
                f"<td><a href=\"{_escape_html(_naver_finance_stock_url(sym))}\" target=\"_blank\" rel=\"noopener noreferrer\">{_escape_html(sym)}</a></td>"
                f"<td class=\"{cls}\">{_fmt_pct(ret)}</td>"
                f"<td>{_fmt_num(px)}</td>"
                f"<td>{_fmt_eok(vt)}</td>"
                f"<td>{_fmt_num(vol)}</td>"
                f"<td>{_escape_html(rk if rk not in (None, '') else '-')}</td>"
                "</tr>"
            )
        parts.append("</tbody></table>")
        parts.append("</section>")
    parts.append("</div></section></div></body></html>")
    return "".join(parts)


def _build_sector_overview(
    idx_rows: list[dict[str, Any]],
    snapshot_by_code: dict[str, dict[str, Any]],
    members_by_code: dict[str, list[dict[str, Any]]],
    *,
    top_n: int,
    include_empty: bool,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in idx_rows:
        code_full = str(item.get("sector_code_full", "")).strip() or str(item.get("sector_code_4", "")).strip()
        api_code = str(item.get("sector_code_4", "")).strip() or code_full[-4:]
        if not code_full or code_full in seen:
            continue
        seen.add(code_full)
        snap = snapshot_by_code.get(code_full) or snapshot_by_code.get(api_code)
        members = list(members_by_code.get(code_full) or [])[:top_n]
        if not include_empty and not members:
            continue
        rows.append(
            {
                "sector_code": code_full,
                "api_sector_code": api_code,
                "sector_name": str(item.get("sector_name", "")).strip() or code_full,
                "market_label": _market_label_for_code(code_full),
                "idx_div": str(item.get("idx_div", "")).strip(),
                "sector_code_full": code_full,
                "snapshot": snap or {},
                "major_stocks": members,
            }
        )
    return _analyze_sector_overview(rows)


def _is_domestic_stock_item(item: dict[str, Any]) -> bool:
    code_full = str(item.get("sector_code_full", "")).strip()
    name = str(item.get("sector_name", "")).strip()
    name_upper = name.upper()
    if not code_full:
        return False
    # 실제 국내 업종 계열은 idxcode 상 0xxxx(KOSPI) / 1xxxx(KOSDAQ) 위주로 사용.
    if code_full[0] not in {"0", "1"}:
        return False
    if any(
        token in name_upper
        for token in (
            "ETF",
            "ETN",
            "ELW",
            "TR",
            "TOTAL RETURN",
            "VIX",
            "BLOOMBERG",
            "DJCI",
            "S&P",
            "NASDAQ",
            "NIKKEI",
            "EURO",
            "HSCEI",
            "MSCI",
            "FTSE",
            "GOLD",
            "COPPER",
            "WTI",
            "SILVER",
            "PLATINUM",
            "레버리지",
            "인버스",
        )
    ):
        return False
    if name in {"종합", "대형주", "중형주", "소형주"}:
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect sector master and optional KIS sector/member data.")
    parser.add_argument("--idxcode-only", action="store_true", help="Only download/parse idxcode.mst to parquet.")
    parser.add_argument("--mode", choices=("mock", "paper", "real"), default="real", help="KIS mode for API steps.")
    parser.add_argument("--sector", default="", help="Sector code (e.g. 0002) for --members.")
    parser.add_argument("--members", action="store_true", help="Fetch volume-rank members for --sector.")
    parser.add_argument("--all-sectors", action="store_true", help="Build full sector overview with top stocks for every sector.")
    parser.add_argument("--top-n", type=int, default=5, help="How many major stocks to keep per sector.")
    parser.add_argument("--include-empty", action="store_true", help="Keep sectors whose major stock list is empty.")
    parser.add_argument("--include-non-domestic", action="store_true", help="Keep non-domestic / overseas / TR / ETF-like index items too.")
    parser.add_argument(
        "--out-dir",
        default="data/lake/sector",
        help="Output directory for parquet/json.",
    )
    parser.add_argument(
        "--telegram",
        action="store_true",
        help="Generate sector overview and send summary + files to Telegram.",
    )
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
        help="Optional Telegram message thread id (forum topic).",
    )
    args = parser.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    from sector_scanner.idxcode_master import load_idxcode_dataframe

    df_idx = load_idxcode_dataframe()
    idx_path = out / "idxcode_sectors.parquet"
    df_idx.to_parquet(idx_path, index=False)
    print(f"Wrote {idx_path} rows={len(df_idx)}")

    if args.idxcode_only:
        return

    if args.members and not str(args.sector).strip():
        print("--members requires --sector <code>", file=sys.stderr)
        sys.exit(2)

    from sector_scanner.kis_client import build_kis_client_for_mode
    from sector_scanner.sector_loader import SectorLoader

    client = build_kis_client_for_mode(args.mode)
    client.authenticate()

    sl = SectorLoader(client)
    sectors = sl.load_sector_snapshots(timezone="Asia/Seoul", use_program_flow=False, use_foreign_institution_flow=False)
    snap_path = out / "sector_snapshots.json"
    snap_path.write_text(
        json.dumps([s.to_dict() for s in sectors], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {snap_path} sectors={len(sectors)}")
    if not sectors:
        print("WARNING: KIS sector snapshot is empty; falling back to idxcode-based sector seeds.")

    if args.members:
        sec = str(args.sector).strip()
        api_sec = sec[-4:] if len(sec) >= 4 else sec
        rows = client.fetch_stocks_in_sector(api_sec)
        mpath = out / f"sector_members_{sec}.json"
        mpath.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote {mpath} members={len(rows)}")

    if args.all_sectors:
        idx_rows = df_idx.to_dict(orient="records")
        if not args.include_non_domestic:
            idx_rows = [r for r in idx_rows if _is_domestic_stock_item(r)]
        seed_rows = _sector_seed_rows(idx_rows, sectors)
        if not args.include_non_domestic:
            seed_rows = [r for r in seed_rows if _is_domestic_stock_item(r) or not str(r.get("idx_div", "")).strip()]
        snapshot_by_code = _sector_snapshot_map(sectors)
        members_by_code: dict[str, list[dict[str, Any]]] = {}
        snapshot_by_full_code: dict[str, dict[str, Any]] = {}
        total = len(seed_rows)
        for i, item in enumerate(seed_rows, start=1):
            code_full = str(item.get("sector_code_full", "")).strip() or str(item.get("sector_code_4", "")).strip()
            api_code = str(item.get("sector_code_4", "")).strip() or code_full[-4:]
            name = str(item.get("sector_name", "")).strip() or code_full
            if not code_full or code_full in members_by_code:
                continue
            snap = snapshot_by_code.get(code_full) or snapshot_by_code.get(api_code)
            if snap is None and hasattr(client, "fetch_sector_snapshot_by_code"):
                collected_at = _now_kst_iso()
                try:
                    snap = client.fetch_sector_snapshot_by_code(api_code)
                except Exception:
                    snap = None
            else:
                collected_at = str((snap or {}).get("as_of") or "").strip() or _now_kst_iso()
            snapshot_by_full_code[code_full] = _augment_snapshot_with_code(
                snap,
                sector_code_full=code_full,
                api_sector_code=api_code,
                sector_name=name,
                collected_at=collected_at,
            )
            rows = client.fetch_stocks_in_sector(api_code)
            rows = sorted(
                rows,
                key=lambda x: (
                    -float(x.get("value_traded", 0.0) or 0.0),
                    int(x.get("rank", 999999) or 999999),
                    str(x.get("symbol", "")),
                ),
            )
            members_by_code[code_full] = rows[: max(1, int(args.top_n))]
            print(f"[{i}/{total}] {name} ({code_full} -> {api_code}) members={len(members_by_code[code_full])}")

        overview = _build_sector_overview(
            seed_rows,
            snapshot_by_full_code,
            members_by_code,
            top_n=max(1, int(args.top_n)),
            include_empty=bool(args.include_empty),
        )
        json_path = out / "sector_overview.json"
        md_path = out / "sector_overview.md"
        html_path = out / "sector_overview.html"
        json_path.write_text(json.dumps(overview, ensure_ascii=False, indent=2), encoding="utf-8")
        md_path.write_text(
            _render_sector_summary_md(overview, top_n=max(1, int(args.top_n))),
            encoding="utf-8",
        )
        html_path.write_text(
            _render_sector_report_html(overview, top_n=max(1, int(args.top_n))),
            encoding="utf-8",
        )
        print(f"Wrote {json_path} sectors={len(overview)}")
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
            _send_telegram_report_bundle(
                overview,
                html_path=html_path,
                bot_token=bot_token,
                chat_id=chat_id,
                thread_id=thread_id,
            )
            print("Telegram report sent.")
    elif args.telegram:
        raise SystemExit("--telegram 은 --all-sectors 와 함께 사용해야 합니다.")


if __name__ == "__main__":
    main()
