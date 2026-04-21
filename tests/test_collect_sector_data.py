from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.collect_sector_data import (
    _analyze_sector_overview,
    _build_telegram_summary,
    _fmt_eok,
    _naver_finance_stock_url,
    _render_sector_report_html,
    _render_sector_summary_md,
)


def test_analyze_sector_overview_adds_relative_strength_and_leader_fields() -> None:
    rows = [
        {
            "sector_code": "00018",
            "api_sector_code": "0018",
            "sector_name": "건설",
            "snapshot": {
                "as_of": "2026-04-21T13:30:00+09:00",
                "current_index": 237.65,
                "return_pct": 0.0608,
                "high_index": 241.93,
                "low_index": 227.93,
                "intraday_trend": 0.0141,
                "acceleration": 0.006,
                "program_flow_strength": 1.2,
                "foreign_institution_flow_strength": 0.8,
            },
            "major_stocks": [
                {"symbol": "047040", "name": "대우건설", "value_traded": 2_700_000_000_000.0, "raw": {"prdy_ctrt": "19.22"}},
                {"symbol": "006360", "name": "GS건설", "value_traded": 380_000_000_000.0, "raw": {"prdy_ctrt": "12.35"}},
                {"symbol": "375500", "name": "DL이앤씨", "value_traded": 190_000_000_000.0, "raw": {"prdy_ctrt": "5.34"}},
            ],
        },
        {
            "sector_code": "00029",
            "api_sector_code": "0029",
            "sector_name": "IT 서비스",
            "snapshot": {
                "as_of": "2026-04-21T13:31:00+09:00",
                "current_index": 1260.0,
                "return_pct": -0.13,
                "high_index": 1274.0,
                "low_index": 1250.0,
                "intraday_trend": -0.0025,
                "acceleration": -0.001,
                "program_flow_strength": -0.5,
                "foreign_institution_flow_strength": -0.5,
            },
            "major_stocks": [
                {"symbol": "035420", "name": "NAVER", "value_traded": 77_000_000_000.0, "raw": {"prdy_ctrt": "0.00"}},
                {"symbol": "035720", "name": "카카오", "value_traded": 38_000_000_000.0, "raw": {"prdy_ctrt": "-0.20"}},
                {"symbol": "036570", "name": "NC", "value_traded": 30_000_000_000.0, "raw": {"prdy_ctrt": "-2.06"}},
            ],
        },
    ]

    analyzed = _analyze_sector_overview(rows)

    assert analyzed[0]["sector_name"] == "건설"
    assert analyzed[0]["analysis"]["relative_strength_rank"] == 1
    assert analyzed[0]["analysis"]["leader_status"] == "주도"
    assert analyzed[0]["analysis"]["leader_candidate"] is True
    assert analyzed[1]["analysis"]["leader_status"] in {"약세", "중립"}

    md = _render_sector_summary_md(analyzed, top_n=3)
    assert "RS: **" in md
    assert "상태: **주도**" in md
    assert "거래대금(억)" in md
    assert "생성 시각:" in md
    assert _fmt_eok(2_700_000_000_000.0) == "27,000.0"

    tg = _build_telegram_summary(analyzed, top_k=2)
    assert "섹터 리포트 생성 완료" in tg
    assert "건설: RS" in tg
    assert _naver_finance_stock_url("005930") == "https://finance.naver.com/item/main.naver?code=005930"

    html = _render_sector_report_html(analyzed, top_n=3)
    assert 'href="https://finance.naver.com/item/main.naver?code=047040"' in html
