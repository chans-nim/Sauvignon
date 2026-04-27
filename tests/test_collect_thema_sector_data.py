from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.collect_thema_sector_data import (
    _build_group_blueprints,
    _build_rows,
    _score_group_members,
    _render_thema_report_html,
    _resolve_classification_json,
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
    live_signal_map = {
        "005930": {"live_signal_score": 70.0, "source_scores": {"volume": 90.0, "return": 60.0}, "source_count": 2},
        "000660": {"live_signal_score": 95.0, "source_scores": {"volume": 98.0, "return": 99.0}, "source_count": 2},
        "042700": {"live_signal_score": 40.0, "source_scores": {"near_high": 80.0}, "source_count": 1},
    }

    major_blueprints, middle_blueprints = _build_group_blueprints(data)
    assert len(major_blueprints) == 1
    assert len(middle_blueprints) == 2

    major_rows = _build_rows(major_blueprints, quotes, live_signal_map, top_n=3)
    middle_rows = _build_rows(middle_blueprints, quotes, live_signal_map, top_n=3)
    memory_row = next(row for row in middle_rows if row["display_path"] == "반도체 > 메모리")

    assert major_rows[0]["display_name"] == "반도체"
    assert major_rows[0]["analysis"]["relative_strength_rank"] == 1
    assert memory_row["major_stocks"][0]["symbol"] == "000660"
    # 중분류도 대분류와 동일: RS 정렬 후 [:top_n]. 구성 종목 < top_n 이면 전부만 표시.
    assert len(memory_row["major_stocks"]) == 2
    assert memory_row["analysis"]["top_stocks_requested"] == 3
    assert memory_row["analysis"]["top_stocks_shown"] == 2
    assert memory_row["analysis"]["top_stocks_peer_total"] == 2
    jangbi = next(row for row in middle_rows if row["display_path"] == "반도체 > 장비")
    assert len(jangbi["major_stocks"]) == 1
    assert jangbi["analysis"]["top_stocks_shown"] == 1

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
    assert "Top3 평균" in html
    assert "<details" in html
    assert 'class="pos"' in html
    assert 'class="neg"' in html
    assert ">RS<" in html
    assert "Thema RS" not in html


def test_resolve_classification_json_falls_back_to_repo_path(monkeypatch, tmp_path) -> None:
    import scripts.collect_thema_sector_data as m

    monkeypatch.setattr(m, "DEFAULT_CLASSIFICATION_JSON", tmp_path / "missing" / "thema.json")
    alt = tmp_path / m._CLASSIFICATION_BASENAME
    alt.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(m, "_repo_classification_json", lambda: alt)
    assert m._resolve_classification_json(m.DEFAULT_CLASSIFICATION_JSON) == alt


def test_resolve_classification_json_returns_existing_file(tmp_path) -> None:
    p = tmp_path / "any.json"
    p.write_text("{}", encoding="utf-8")
    assert _resolve_classification_json(p) == p


def test_middle_group_top_n_is_rs_sorted_slice() -> None:
    """중분류 구성 종목이 top_n보다 많을 때, 정확히 top_n개·RS 내림차순인지 확인."""
    data = {
        "major_categories": [
            {
                "majorCategory": "테스트",
                "subCategories": [
                    {
                        "middleCategory": "다종목",
                        "stocks": [
                            {"stockCode": "111110", "stockName": "일", "marketCap": 100},
                            {"stockCode": "222220", "stockName": "이", "marketCap": 100},
                            {"stockCode": "333330", "stockName": "삼", "marketCap": 100},
                            {"stockCode": "444440", "stockName": "사", "marketCap": 100},
                        ],
                    }
                ],
            }
        ]
    }
    _, middle_blueprints = _build_group_blueprints(data)
    assert len(middle_blueprints) == 1
    quotes: dict = {}
    live_signal_map = {
        "111110": {"live_signal_score": 10.0, "source_scores": {"return": 100.0}, "source_count": 1},
        "222220": {"live_signal_score": 40.0, "source_scores": {"return": 100.0}, "source_count": 1},
        "333330": {"live_signal_score": 20.0, "source_scores": {"return": 100.0}, "source_count": 1},
        "444440": {"live_signal_score": 30.0, "source_scores": {"return": 100.0}, "source_count": 1},
    }
    scored = _score_group_members(middle_blueprints[0]["stocks"], quotes, live_signal_map)
    assert [r["symbol"] for r in scored[:2]] == ["222220", "444440"]

    rows = _build_rows(middle_blueprints, quotes, live_signal_map, top_n=2)
    assert len(rows[0]["major_stocks"]) == 2
    assert rows[0]["analysis"]["top_stocks_requested"] == 2
    assert rows[0]["analysis"]["top_stocks_shown"] == 2
    assert rows[0]["analysis"]["top_stocks_peer_total"] == 4
    assert [m["symbol"] for m in rows[0]["major_stocks"]] == ["222220", "444440"]
