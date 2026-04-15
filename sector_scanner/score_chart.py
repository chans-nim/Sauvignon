"""스캐너 점수 시계열 기록 및 Plotly HTML 차트."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .models import ScanResult


@dataclass(frozen=True)
class ScorePoint:
    ts: pd.Timestamp
    symbol: str
    name: str
    score: float
    source: str
    delta_from_prev: float | None


class ScoreHistoryBuffer:
    """
    full_scan / realtime 시점별 점수를 누적하고 HTML 라인 차트로보낸다.

    범례·호버에 ``코드 종목명`` 형식으로 표시한다.
    """

    def __init__(self, max_points: int = 12_000) -> None:
        self._max_points = max(100, int(max_points))
        self._rows: list[ScorePoint] = []
        self._last_score: dict[str, float] = {}

    def clear(self) -> None:
        self._rows.clear()
        self._last_score.clear()

    def _append(self, p: ScorePoint) -> None:
        self._rows.append(p)
        if len(self._rows) > self._max_points:
            drop = len(self._rows) - self._max_points
            self._rows = self._rows[drop:]

    def record_scan_result(self, result: ScanResult, *, only_passed: bool = True) -> None:
        """전체 스캔 직후: 통과 종목(또는 전체) 점수 기록."""
        ts = result.as_of
        if getattr(ts, "tzinfo", None) is None:
            ts = pd.Timestamp(ts).tz_localize("Asia/Seoul")
        for st in result.all_stock_scores:
            if only_passed and not st.passed_filters:
                continue
            sym = str(st.symbol).strip()
            name = (st.name or sym).strip() or sym
            sc = float(st.score)
            prev = self._last_score.get(sym)
            delta = (sc - prev) if prev is not None else None
            self._last_score[sym] = sc
            self._append(
                ScorePoint(
                    ts=ts,
                    symbol=sym,
                    name=name,
                    score=sc,
                    source="full_scan",
                    delta_from_prev=delta,
                )
            )

    def record_runtime_scores(self, runtime: dict[str, Any], *, only_passed: bool = True) -> None:
        """``stock_scores_realtime`` JSON 블록에서 점수 기록."""
        raw = runtime.get("stock_scores_realtime")
        if not isinstance(raw, list):
            return
        raw_ts = runtime.get("realtime_as_of")
        try:
            ts = pd.Timestamp(raw_ts) if raw_ts else pd.Timestamp.now(tz="Asia/Seoul")
            if ts.tzinfo is None:
                ts = ts.tz_localize("Asia/Seoul")
            else:
                ts = ts.tz_convert("Asia/Seoul")
        except (ValueError, TypeError, pd.errors.OutOfBoundsDatetime):
            ts = pd.Timestamp.now(tz="Asia/Seoul")

        for item in raw:
            if not isinstance(item, dict):
                continue
            if only_passed and not bool(item.get("passed_filters", False)):
                continue
            sym = str(item.get("symbol", "")).strip()
            if not sym:
                continue
            name = str(item.get("name", sym) or sym).strip()
            try:
                sc = float(item.get("score", 0.0))
            except (TypeError, ValueError):
                continue
            prev = self._last_score.get(sym)
            delta = (sc - prev) if prev is not None else None
            self._last_score[sym] = sc
            self._append(
                ScorePoint(
                    ts=ts,
                    symbol=sym,
                    name=name,
                    score=sc,
                    source="realtime",
                    delta_from_prev=delta,
                )
            )

    def to_dataframe(self) -> pd.DataFrame:
        if not self._rows:
            return pd.DataFrame(columns=["ts", "symbol", "name", "score", "source", "delta_from_prev", "label", "delta_str"])
        df = pd.DataFrame(
            [
                {
                    "ts": p.ts,
                    "symbol": p.symbol,
                    "name": p.name,
                    "score": p.score,
                    "source": p.source,
                    "delta_from_prev": p.delta_from_prev,
                }
                for p in self._rows
            ]
        )
        df["label"] = df["symbol"].astype(str) + " " + df["name"].astype(str)
        def _fmt_delta(x: object) -> str:
            if x is None:
                return "—"
            try:
                return f"{float(x):+.4f}"
            except (TypeError, ValueError):
                return "—"

        df["delta_str"] = df["delta_from_prev"].map(_fmt_delta)
        return df

    def write_plotly_html(self, path: str | Path, *, title: str = "스캐너 점수 변화") -> bool:
        """
        종목별 score 시계열 라인 차트 HTML 저장.

        Returns
        -------
        bool
            성공 여부 (plotly 미설치 등으로 실패 시 False).
        """
        try:
            import plotly.graph_objects as go
        except ImportError:
            return False

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        df = self.to_dataframe()
        if df.empty:
            fig = go.Figure()
            fig.add_annotation(
                text="기록된 점수가 없습니다. 스캔 또는 실시간 갱신 후 다시 확인하세요.",
                xref="paper",
                yref="paper",
                x=0.5,
                y=0.5,
                showarrow=False,
            )
            fig.update_layout(title=title, template="plotly_white", height=480)
            fig.write_html(str(path), include_plotlyjs="cdn", config={"displayModeBar": True})
            return True

        fig = go.Figure()
        for label, sub in df.groupby("label", sort=False):
            sub2 = sub.sort_values("ts")
            fig.add_trace(
                go.Scatter(
                    x=sub2["ts"],
                    y=sub2["score"],
                    name=str(label),
                    mode="lines+markers",
                    customdata=sub2[["delta_str", "source"]].values,
                    hovertemplate=(
                        "<b>%{fullData.name}</b><br>"
                        "시각: %{x}<br>"
                        "점수: %{y:.4f}<br>"
                        "직전 대비: %{customdata[0]}<br>"
                        "출처: %{customdata[1]}<extra></extra>"
                    ),
                )
            )

        fig.update_layout(
            title=title,
            xaxis_title="시각",
            yaxis_title="score",
            template="plotly_white",
            height=max(520, min(900, 120 + 40 * df["label"].nunique())),
            legend=dict(orientation="v", yanchor="top", y=1, xanchor="left", x=1.02),
            margin=dict(l=60, r=180, t=60, b=60),
        )
        fig.write_html(str(path), include_plotlyjs="cdn", config={"displayModeBar": True})
        return True
