"""백테스트 수치 요약을 한국어 해석 문단으로 변환한다 (투자 조언 아님, 결과 설명용)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from metrics import PerformanceSummary


@dataclass(frozen=True)
class RunContext:
    """해석에 붙일 실행 맥락."""

    data_source: str
    start_date: str
    end_date: str
    symbols: tuple[str, ...]
    n_trades: int
    n_equity_days: int


def build_interpretation_ko(summary: PerformanceSummary, ctx: RunContext) -> str:
    """
    성과 지표를 문장으로 풀어 쓴다.

    주의: 과거 구간·단순 규칙 기반 시뮬레이션에 대한 설명이며,
    미래 수익을 보장하지 않는다는 전제를 포함한다.
    """
    d: dict[str, Any] = summary.to_dict()
    tr = float(d["total_return"])
    cagr = float(d["cagr"])
    mdd = float(d["mdd"])
    wr = float(d["win_rate"])
    pf = float(d["profit_factor"])
    ag = float(d["avg_gain"])
    al = float(d["avg_loss"])
    hold = float(d["avg_holding_days"])

    lines: list[str] = []
    lines.append("## 백테스트 결과 해석 (요약)")
    lines.append("")
    lines.append(
        f"- **데이터**: {ctx.data_source} | **기간**: {ctx.start_date} ~ {ctx.end_date} | "
        f"**종목**: {', '.join(ctx.symbols)} | **거래 건수(레코드)**: {ctx.n_trades} | **자산 곡선 일수**: {ctx.n_equity_days}"
    )
    lines.append("")

    if tr > 0.05:
        lines.append(
            f"- **누적 수익률 약 {tr:.1%}**로, 검토 구간 동안 초기 자본 대비 순자산이 증가한 시뮬레이션 결과입니다."
        )
    elif tr < -0.05:
        lines.append(
            f"- **누적 수익률 약 {tr:.1%}**로, 해당 구간·규칙 조합에서는 손실이 우세했습니다."
        )
    else:
        lines.append(
            f"- **누적 수익률 약 {tr:.1%}**로, 거의 보합에 가까운 결과입니다."
        )

    if abs(cagr) > 1e-6:
        lines.append(
            f"- **연율화 수익률(CAGR) 약 {cagr:.1%}**는 자산 곡선의 시작·종료 시점과 기간 길이로 환산한 값입니다. "
            "기간이 짧으면 CAGR 변동이 크게 나올 수 있습니다."
        )

    lines.append(
        f"- **최대 낙폭(MDD) 약 {abs(mdd):.1%}**는 곡선 고점 대비 최저 하락 폭입니다. "
        "수익률이 좋아도 MDD가 크면 변동성·심리적 부담이 컸을 수 있습니다."
    )

    lines.append("")
    lines.append("### 거래 통계 관점")
    if ctx.n_trades == 0:
        lines.append("- 체결된 거래가 없어 승률·손익비는 참고값에 가깝습니다(표본 부족).")
    else:
        lines.append(
            f"- **승률 약 {wr:.0%}**: 전체 라운드 트립 기준 이익 실현 비율입니다."
        )
        if pf >= 900:
            lines.append(
                "- **손익비**: 손실 라운드가 거의 없어 수치가 매우 크게 나올 수 있습니다(거래 수가 적으면 과대 해석 주의)."
            )
        elif pf >= 1.2:
            lines.append(
                f"- **손익비(Profit factor) 약 {pf:.2f}**: 총 이익이 총 손실을 상회하는 패턴입니다."
            )
        elif 0 < pf < 1.0:
            lines.append(
                f"- **손익비 약 {pf:.2f}**: 손실 거래의 합이 이익 거래 합을 압도하는 구간입니다."
            )
        else:
            lines.append(f"- **손익비 약 {pf:.2f}** (표본·규칙에 따라 해석이 달라질 수 있음).")

        if wr < 0.35 and ag > abs(al) and al != 0:
            lines.append(
                "- 승률은 낮지만 **평균 이익이 평균 손실보다 큰** 형태입니다. "
                "소수의 큰 수익이 결과를 끌어올렸을 가능성이 있어, 표본이 작으면 과대평가되기 쉽습니다."
            )
        elif wr > 0.5 and pf < 1.0:
            lines.append(
                "- 승률은 높지만 손익비가 1 미만이면 **작게 자주 이기고 크게 지는** 패턴일 수 있습니다."
            )

    lines.append(
        f"- **평균 보유일 약 {hold:.1f}일**: 전략이 포지션을 유지한 평균 기간입니다."
    )

    lines.append("")
    lines.append("### 해석 시 유의사항")
    lines.append(
        "- 본 엔진은 **수수료·세금·슬리피지·유동성**을 단순 모델로 반영합니다. 실제 체결과 차이가 납니다."
    )
    lines.append(
        "- **동일 캘린더 교집합**으로 여러 종목을 묶어 백테스트하므로, 상장일·거래정지 등으로 공통 거래일이 줄면 표본이 짧아질 수 있습니다."
    )
    lines.append(
        "- **과거 데이터에 최적화된 규칙**은 미래에 약화될 수 있습니다. 라이브 전에는 표본 외 기간·종목으로 검증하는 것이 좋습니다."
    )

    return "\n".join(lines)
