"""
WebSocket 체결 틱 기반 실시간 점수 보정 (최근 N분 윈도).

- 최근 5분 거래강도(틱 거래량 합 정규화)
- 세션 고점 대비 고점 갱신 여부
- VWAP 대비 마지막 체결이 하방 이탈인지
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from .models import StockScore, StockSnapshot


def _parse_ts(ev: dict[str, Any], default_tz: str) -> pd.Timestamp | None:
    raw = ev.get("ts") or ev.get("timestamp")
    if raw is None:
        return None
    try:
        t = pd.Timestamp(raw)
        if t.tzinfo is None:
            t = t.tz_localize(default_tz)
        else:
            t = t.tz_convert(default_tz)
        return t
    except (ValueError, TypeError, pd.errors.OutOfBoundsDatetime):
        return None


def merge_tick_buffer(
    prev: dict[str, list[dict[str, Any]]],
    new_events: list[dict[str, Any]],
    *,
    timezone: str,
    window_minutes: float,
    now: pd.Timestamp,
) -> dict[str, list[dict[str, Any]]]:
    """심볼별 틱 버퍼에 신규 이벤트를 넣고 윈도우 밖은 제거."""
    buf: dict[str, list[dict[str, Any]]] = {k: list(v) for k, v in prev.items() if isinstance(v, list)}
    cutoff = now - pd.Timedelta(minutes=window_minutes)

    for ev in new_events:
        if not isinstance(ev, dict):
            continue
        sym = str(ev.get("symbol", "")).strip()
        if not sym:
            continue
        ts = _parse_ts(ev, timezone)
        if ts is None:
            ts = now
        row = {
            "ts": ts.isoformat(),
            "price": float(ev.get("price", 0.0) or 0.0),
            "volume": float(ev.get("volume", 0.0) or 0.0),
        }
        buf.setdefault(sym, []).append(row)

    pruned: dict[str, list[dict[str, Any]]] = {}
    for sym, rows in buf.items():
        keep: list[dict[str, Any]] = []
        for r in rows:
            try:
                t = pd.Timestamp(r["ts"])
                if t.tzinfo is None:
                    t = t.tz_localize(timezone)
                else:
                    t = t.tz_convert(timezone)
            except (KeyError, ValueError, TypeError, pd.errors.OutOfBoundsDatetime):
                continue
            if t >= cutoff:
                keep.append(r)
        if keep:
            pruned[sym] = keep
    return pruned


def _rows_in_window(
    buf: dict[str, list[dict[str, Any]]],
    symbol: str,
    now: pd.Timestamp,
    window_minutes: float,
    timezone: str,
) -> list[dict[str, Any]]:
    sym = str(symbol).strip()
    rows = buf.get(sym) or []
    cutoff = now - pd.Timedelta(minutes=window_minutes)
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            t = pd.Timestamp(r["ts"])
            if t.tzinfo is None:
                t = t.tz_localize(timezone)
            else:
                t = t.tz_convert(timezone)
        except (KeyError, ValueError, TypeError, pd.errors.OutOfBoundsDatetime):
            continue
        if t >= cutoff:
            out.append(r)
    out.sort(key=lambda x: x.get("ts", ""))
    return out


def trade_intensity_norm_5m(
    buf: dict[str, list[dict[str, Any]]],
    symbol: str,
    *,
    now: pd.Timestamp,
    window_minutes: float,
    timezone: str,
    ref_volume_sum: float = 80_000.0,
) -> float:
    """0~1 근사: 최근 윈도우 틱 거래량 합을 ref 대비 로그 스케일로 정규화."""
    rows = _rows_in_window(buf, symbol, now, window_minutes, timezone)
    s = sum(max(0.0, float(r.get("volume", 0.0))) for r in rows)
    if ref_volume_sum <= 0:
        return 0.0
    return max(0.0, min(1.0, math.log1p(s) / math.log1p(ref_volume_sum)))


def made_new_high_in_window(
    buf: dict[str, list[dict[str, Any]]],
    symbol: str,
    session_high: float | None,
    *,
    now: pd.Timestamp,
    window_minutes: float,
    timezone: str,
    eps: float = 1e-6,
) -> bool:
    """윈도우 내 최고 체결가가 당일(스냅샷) 고가 이상이면 고점 갱신으로 본다."""
    if session_high is None or session_high <= 0:
        return False
    rows = _rows_in_window(buf, symbol, now, window_minutes, timezone)
    if not rows:
        return False
    mx = max(float(r.get("price", 0.0)) for r in rows)
    return mx >= float(session_high) * (1.0 - eps)


def last_tick_below_vwap(
    buf: dict[str, list[dict[str, Any]]],
    symbol: str,
    vwap: float | None,
    *,
    now: pd.Timestamp,
    window_minutes: float,
    timezone: str,
) -> bool:
    """마지막 체결이 VWAP 미만이면 하방 이탈."""
    if vwap is None or vwap <= 0:
        return False
    rows = _rows_in_window(buf, symbol, now, window_minutes, timezone)
    if not rows:
        return False
    last_px = float(rows[-1].get("price", 0.0))
    return last_px < float(vwap)


def adjust_scores_with_ticks(
    scores: list[StockScore],
    snapshots: dict[str, StockSnapshot],
    tick_buffer: dict[str, list[dict[str, Any]]],
    *,
    timezone: str,
    window_minutes: float = 5.0,
    weight_intensity: float = 8.0,
    bonus_new_high: float = 4.0,
    penalty_below_vwap: float = 5.0,
) -> list[StockScore]:
    """
    StockScore.score에 실시간 보정을 가산하고 factors에 근거를 남긴다.

    - 거래강도: weight_intensity * intensity_norm
    - 고점 갱신: +bonus_new_high
    - VWAP 이탈(하방): -penalty_below_vwap
    """
    now = pd.Timestamp.now(tz=timezone)
    out: list[StockScore] = []
    for s in scores:
        snap = snapshots.get(s.symbol)
        if snap is None:
            out.append(s)
            continue

        intensity = trade_intensity_norm_5m(
            tick_buffer, s.symbol, now=now, window_minutes=window_minutes, timezone=timezone
        )
        nh = made_new_high_in_window(
            tick_buffer,
            s.symbol,
            snap.high,
            now=now,
            window_minutes=window_minutes,
            timezone=timezone,
        )
        below = last_tick_below_vwap(
            tick_buffer, s.symbol, snap.vwap, now=now, window_minutes=window_minutes, timezone=timezone
        )

        delta = weight_intensity * intensity
        if nh:
            delta += bonus_new_high
        if below:
            delta -= penalty_below_vwap

        base = float(s.score)
        new_score = max(0.0, min(100.0, base + delta))
        factors = dict(s.factors)
        factors.update(
            {
                "base_score_before_realtime": round(base, 4),
                "realtime_trade_intensity_norm_5m": round(intensity, 6),
                "realtime_new_high": nh,
                "realtime_below_vwap": below,
                "realtime_delta": round(delta, 4),
            }
        )
        out.append(
            StockScore(
                symbol=s.symbol,
                name=s.name,
                sector_code=s.sector_code,
                sector_name=s.sector_name,
                score=round(new_score, 4),
                factors=factors,
                passed_filters=s.passed_filters,
                reject_reason=s.reject_reason,
            )
        )
    return out
