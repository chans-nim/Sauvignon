from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.collect_thema_sector_data import (
    _build_group_blueprints,
    _build_rows,
    _render_thema_report_html,
)


def test_build_thema_rows_and_render_html() -> None:
    data = {
        "schema_version": "2.0",
        "major_category_count": 1,
        "middle_category_count": 2,
        "unique_stock_count": 3,
        "major_categories": [
            {
                "majorCategory": "반도체",
                "subCategories": [
                    {
                        "middleCategory": "메모리",
                        "stocks": [
                            {"stockCode": "005930", "stockName": "삼성전자", "marketCap": 1_280_000, "rs": 92},
                            {"stockCode": "000660", "stockName": "SK하이닉스", "marketCap": 870_000, "rs": 97},
                        ],
                    },
                    {
                        "middleCategory": "장비",
                        "stocks": [
                            {"stockCode": "042700", "stockName": "한미반도체", "marketCap": 280_000, "rs": 88},
                        ],
                    },
                ],
            }
        ],
    }
    quotes = {
        "005930": {"price": 219000, "return_pct": 0.021, "value_traded": 364_619_000_000, "volume": 16_705_245},
        "000660": {"price": 1224000, "return_pct": 0.0497, "value_traded": 2_800_000_000_000, "volume": 2_300_000},
        "042700": {"price": 295000, "return_pct": -0.0279, "value_traded": 150_000_000_000, "volume": 500_000},
    }

    major_blueprints, middle_blueprints = _build_group_blueprints(data)
    assert len(major_blueprints) == 1
    assert len(middle_blueprints) == 2

    major_rows = _build_rows(major_blueprints, quotes, top_n=3)
    middle_rows = _build_rows(middle_blueprints, quotes, top_n=3)

    assert major_rows[0]["display_name"] == "반도체"
    assert major_rows[0]["analysis"]["relative_strength_rank"] == 1
    assert middle_rows[0]["major_stocks"][0]["symbol"] in {"005930", "000660"}

    html = _render_thema_report_html(
        source_path=Path("dummy.json"),
        collected_at="2026-04-21T16:10:00+09:00",
        meta={
            "schema_version": "2.0",
            "major_category_count": 1,
            "middle_category_count": 2,
            "unique_stock_count": 3,
            "quote_symbol_count": 3,
        },
        major_rows=major_rows,
        middle_rows=middle_rows,
        top_n=3,
    )
    assert "Thema Major/Middle Sector Overview" in html
    assert 'href="https://finance.naver.com/item/main.naver?code=005930"' in html
    assert "대분류 Leaderboard" in html
    assert "<details" in html
    assert 'class="pos"' in html
    assert 'class="neg"' in html
