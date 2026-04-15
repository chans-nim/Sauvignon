"""
Strategy Lab HTML 리포트: 최종 랭킹(전략별 top-K 풀) + 포트폴리오 지표 차트 + 종목별 매매 마커.
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

import pandas as pd

from metrics import PerformanceSummary


def final_ranking_from_top_pool(top_pool: pd.DataFrame) -> pd.DataFrame:
    """
    옵션 B: 전략별로 선별된 행들만 모은 뒤, CAGR 기준으로 전체 재정렬한 '최종 랭킹'.
    """
    if top_pool.empty:
        return top_pool
    ok = top_pool[top_pool.get("error").isna()] if "error" in top_pool.columns else top_pool
    if ok.empty or "cagr" not in ok.columns:
        return ok
    return ok.sort_values("cagr", ascending=False).reset_index(drop=True)


def _equity_series(equity_df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    if equity_df.empty or "date" not in equity_df.columns or "equity" not in equity_df.columns:
        return pd.Series(dtype=float), pd.Series(dtype="datetime64[ns]")
    ed = equity_df.sort_values("date").reset_index(drop=True)
    d = pd.to_datetime(ed["date"])
    e = ed["equity"].astype(float)
    return e, d


def _drawdown_pct(equity: pd.Series) -> pd.Series:
    if equity.empty:
        return equity
    peak = equity.cummax()
    return (equity / peak - 1.0) * 100.0


def _fig_portfolio_metrics(summary: PerformanceSummary, equity_df: pd.DataFrame) -> Any:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    e, dates = _equity_series(equity_df)
    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.07,
        row_heights=[0.38, 0.32, 0.30],
        subplot_titles=("포트폴리오 자산 & 누적 수익률", "낙폭 (Drawdown)", "요약 지표 (CAGR · 누적수익 · MDD)"),
        specs=[[{"secondary_y": True}], [{}], [{}]],
    )

    if not e.empty and e.iloc[0] > 0:
        cum_ret_pct = (e / float(e.iloc[0]) - 1.0) * 100.0
        dd_pct = _drawdown_pct(e)
        fig.add_trace(
            go.Scatter(x=dates, y=e, name="Equity", line=dict(color="#2563eb", width=2), mode="lines"),
            row=1,
            col=1,
            secondary_y=False,
        )
        fig.add_trace(
            go.Scatter(
                x=dates,
                y=cum_ret_pct,
                name="누적 수익률 %",
                line=dict(color="#16a34a", width=2, dash="dot"),
                mode="lines",
            ),
            row=1,
            col=1,
            secondary_y=True,
        )
        fig.update_yaxes(title_text="Equity (원)", row=1, col=1, secondary_y=False)
        fig.update_yaxes(title_text="누적 수익률 %", row=1, col=1, secondary_y=True)

        fig.add_trace(
            go.Scatter(
                x=dates,
                y=dd_pct,
                name="DD %",
                line=dict(color="#dc2626", width=1.5),
                fill="tozeroy",
                mode="lines",
            ),
            row=2,
            col=1,
        )
        fig.update_yaxes(title_text="낙폭 %", row=2, col=1)
    else:
        fig.add_annotation(
            text="에퀴티 데이터 없음",
            xref="x domain",
            yref="y domain",
            x=0.5,
            y=0.8,
            showarrow=False,
            row=1,
            col=1,
        )

    tr = summary.total_return * 100.0
    cg = summary.cagr * 100.0
    mdd = summary.mdd * 100.0
    fig.add_trace(
        go.Bar(
            x=["CAGR", "누적 수익률", "MDD"],
            y=[cg, tr, mdd],
            marker_color=["#2563eb", "#16a34a", "#dc2626"],
            text=[f"{cg:.2f}%", f"{tr:.2f}%", f"{mdd:.2f}%"],
            textposition="outside",
            name="요약",
        ),
        row=3,
        col=1,
    )
    fig.update_yaxes(title_text="%", row=3, col=1)

    fig.update_layout(
        height=920,
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=55, r=55, t=72, b=40),
        template="plotly_white",
    )
    fig.update_xaxes(title_text="날짜", row=2, col=1)
    fig.update_xaxes(title_text="", row=3, col=1)
    return fig


def _fig_symbol_trades(symbol: str, ohlcv: pd.DataFrame, trades: pd.DataFrame) -> Any:
    import plotly.graph_objects as go

    if ohlcv is None or ohlcv.empty:
        fig = go.Figure()
        fig.add_annotation(text=f"{symbol}: OHLCV 없음", xref="paper", yref="paper", x=0.5, y=0.5, showarrow=False)
        fig.update_layout(template="plotly_white", height=320, title=symbol)
        return fig

    df = ohlcv.sort_index()
    x = pd.to_datetime(df.index)

    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=x,
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name="OHLC",
            increasing_line_color="#16a34a",
            decreasing_line_color="#dc2626",
        )
    )

    if not trades.empty:
        td = trades.copy()
        td["date"] = pd.to_datetime(td["date"]).dt.normalize()
        buys = td[td["side"].astype(str).str.upper() == "BUY"]
        sells = td[td["side"].astype(str).str.upper() == "SELL"]
        if not buys.empty:
            fig.add_trace(
                go.Scatter(
                    x=buys["date"],
                    y=buys["price"],
                    mode="markers",
                    name="매수",
                    marker=dict(symbol="triangle-up", size=11, color="#2563eb", line=dict(width=1, color="white")),
                )
            )
        if not sells.empty:
            fig.add_trace(
                go.Scatter(
                    x=sells["date"],
                    y=sells["price"],
                    mode="markers",
                    name="매도",
                    marker=dict(symbol="triangle-down", size=11, color="#ea580c", line=dict(width=1, color="white")),
                )
            )

    fig.update_layout(
        title=f"{symbol} — 가격 & 매매",
        xaxis_rangeslider_visible=False,
        height=400,
        template="plotly_white",
        margin=dict(l=50, r=30, t=50, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig


def write_strategy_lab_html(
    path: Path,
    *,
    title: str,
    best_strategy_id: str,
    best_params: dict[str, Any],
    summary: PerformanceSummary,
    equity_curve_df: pd.DataFrame,
    trade_log_df: pd.DataFrame,
    ohlcv_by_symbol: dict[str, pd.DataFrame],
    final_ranking_df: pd.DataFrame,
    max_symbol_charts: int = 100,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    parts: list[str] = []
    parts.append("<!DOCTYPE html>\n<html lang=\"ko\">\n<head>\n<meta charset=\"utf-8\"/>\n")
    parts.append(f"<title>{escape(title)}</title>\n")
    parts.append(
        "<style>body{font-family:system-ui,Segoe UI,Roboto,sans-serif;margin:24px;background:#fafafa;color:#111;}"
        "h1{font-size:1.35rem;} h2{font-size:1.1rem;margin-top:2rem;} table{border-collapse:collapse;width:100%;background:#fff;}"
        "th,td{border:1px solid #e5e7eb;padding:6px 8px;font-size:0.85rem;text-align:left;} th{background:#f3f4f6;}"
        ".meta{color:#4b5563;font-size:0.9rem;margin-bottom:1rem;} .chart{margin-bottom:2rem;}</style>\n</head>\n<body>\n"
    )
    parts.append(f"<h1>{escape(title)}</h1>\n")
    parts.append("<div class=\"meta\">")
    parts.append(f"<div><strong>선택 전략</strong>: <code>{escape(best_strategy_id)}</code></div>")
    if best_params:
        parts.append(f"<div><strong>파라미터</strong>: <code>{escape(json.dumps(best_params, ensure_ascii=False))}</code></div>")
    parts.append(
        f"<div><strong>CAGR</strong> {summary.cagr * 100:.2f}% &nbsp;|&nbsp; "
        f"<strong>누적 수익률</strong> {summary.total_return * 100:.2f}% &nbsp;|&nbsp; "
        f"<strong>MDD</strong> {summary.mdd * 100:.2f}%</div>"
    )
    parts.append("</div>\n")

    parts.append("<h2>최종 랭킹 (전략별 Top-K 풀 → CAGR 재정렬)</h2>\n")
    if final_ranking_df is not None and not final_ranking_df.empty:
        cols = [c for c in final_ranking_df.columns if c not in ("error",)]
        view = final_ranking_df[cols].head(40).copy()
        parts.append("<table>\n<thead><tr>")
        for c in view.columns:
            parts.append(f"<th>{escape(str(c))}</th>")
        parts.append("</tr></thead>\n<tbody>\n")
        for _, row in view.iterrows():
            parts.append("<tr>")
            for c in view.columns:
                v = row[c]
                s = "" if pd.isna(v) else str(v)
                parts.append(f"<td>{escape(s)}</td>")
            parts.append("</tr>\n")
        parts.append("</tbody></table>\n")
    else:
        parts.append("<p>랭킹 데이터 없음</p>\n")

    fig_p = _fig_portfolio_metrics(summary, equity_curve_df)
    parts.append('<div class="chart">')
    parts.append(fig_p.to_html(full_html=False, include_plotlyjs="cdn", config={"displayModeBar": True}))
    parts.append("</div>\n")

    parts.append("<h2>종목별 차트 (매수·매도)</h2>\n")
    parts.append("<p class=\"meta\">거래가 발생한 종목 위주이며, 차트 수는 상한으로 제한됩니다.</p>\n")

    syms: list[str] = []
    if trade_log_df is not None and not trade_log_df.empty and "symbol" in trade_log_df.columns:
        syms = sorted(trade_log_df["symbol"].astype(str).unique().tolist())
    syms = syms[: max(0, int(max_symbol_charts))]

    for sym in syms:
        sub = trade_log_df[trade_log_df["symbol"].astype(str) == sym]
        ohl = ohlcv_by_symbol.get(sym)
        fig_s = _fig_symbol_trades(sym, ohl if ohl is not None else pd.DataFrame(), sub)
        parts.append('<div class="chart">')
        parts.append(fig_s.to_html(full_html=False, include_plotlyjs=False, config={"displayModeBar": True}))
        parts.append("</div>\n")

    parts.append("</body>\n</html>")
    path.write_text("".join(parts), encoding="utf-8")
