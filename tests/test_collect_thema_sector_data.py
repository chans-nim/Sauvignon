from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.collect_thema_sector_data import (
    _all_unique_stock_symbols_from_groups,
    _build_group_blueprints,
    _build_rows,
    _build_theme_daily_leader_history_sections,
    _render_theme_history_calendar_html,
    classify_theme_quality,
    calculate_breadth_score,
    calculate_persistence_score,
    _score_group_members,
    _select_quote_symbols,
    _render_theme_report_html,
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

    investor_map = {
        "005930": {"foreign_net_tr_pbmn": 500_000_000_000, "institution_net_tr_pbmn": -120_000_000_000},
        "000660": {"foreign_net_tr_pbmn": 50_000_000_000, "institution_net_tr_pbmn": 30_000_000_000},
        "042700": {"foreign_net_tr_pbmn": -10_000_000_000, "institution_net_tr_pbmn": 5_000_000_000},
    }
    program_map = {
        "005930": {"program_net_tr_pbmn": 30_000_000_000},
        "000660": {"program_net_tr_pbmn": 10_000_000_000},
        "042700": {"program_net_tr_pbmn": 0},
    }

    major_rows = _build_rows(
        major_blueprints,
        quotes,
        live_signal_map,
        top_n=3,
        investor_by_symbol=investor_map,
        program_by_symbol=program_map,
    )
    middle_rows = _build_rows(
        middle_blueprints,
        quotes,
        live_signal_map,
        top_n=3,
        investor_by_symbol=investor_map,
        program_by_symbol=program_map,
    )
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

    html = _render_theme_report_html(
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
    assert "Theme Major/Middle Sector Overview" in html
    assert 'href="https://finance.naver.com/item/main.naver?code=005930"' in html
    assert "외국인 순매수(억)" in html
    assert "프로그램 순매수(억)" in html
    assert "+5,000.0" in html
    assert "대분류 Leaderboard" in html
    assert "ThemeScoreV2" in html
    assert "지속성" in html
    assert "확산도" in html
    assert "테마 품질" in html
    assert "Top3 평균" in html
    assert "<details" in html
    assert 'class="pos"' in html
    assert 'class="neg"' in html
    assert ">RS<" in html
    assert "Theme RS" not in html
    assert "주도테마 캘린더·히스토리" in html
    assert "cal-wrap" in html


def test_build_theme_daily_leader_history_sections_prefers_today_live() -> None:
    history_rows = [
        {
            "date": "2026-05-01",
            "group_type": "middle",
            "major_category": "A",
            "middle_category": "B",
            "leader_status": "주도",
            "theme_rs": 70.0,
            "theme_rank": 1,
            "leader_top_stocks": [{"symbol": "000001", "name": "Old", "rs": 50.0}],
        }
    ]
    major_rows: list[dict] = []
    middle_rows = [
        {
            "group_type": "middle",
            "display_path": "A > B",
            "major_category": "A",
            "middle_category": "B",
            "major_stocks": [{"symbol": "000002", "name": "Live", "rs": 90.0}],
            "analysis": {"leader_status": "주도", "relative_strength_score": 88.0, "relative_strength_rank": 1},
        }
    ]
    sections = _build_theme_daily_leader_history_sections(
        history_rows,
        "2026-05-10",
        major_rows,
        middle_rows,
        top_n=3,
        max_days=5,
    )
    by_d = dict(sections)
    assert "2026-05-10" in by_d
    assert by_d["2026-05-10"][0]["leader_top_stocks"][0]["symbol"] == "000002"
    assert "2026-05-01" in by_d


def test_theme_history_calendar_shows_one_stock_per_theme() -> None:
    by_date = {
        "2026-05-10": [
            {
                "display_path": "반도체 > 메모리",
                "leader_top_stocks": [
                    {"symbol": "000660", "name": "SK", "rs": 90.0},
                    {"symbol": "005930", "name": "SEC", "rs": 50.0},
                ],
            }
        ]
    }
    html = _render_theme_history_calendar_html(by_date, "2026-05-10")
    assert "SK" in html and "000660" in html
    assert "005930" not in html


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


def test_all_unique_stock_symbols_from_groups() -> None:
    data = {
        "major_categories": [
            {
                "majorCategory": "M",
                "subCategories": [
                    {
                        "middleCategory": "A",
                        "stocks": [
                            {"stockCode": "1", "stockName": "a", "marketCap": 1},
                            {"stockCode": "2", "stockName": "b", "marketCap": 2},
                        ],
                    },
                ],
            }
        ]
    }
    major_b, middle_b = _build_group_blueprints(data)
    syms = _all_unique_stock_symbols_from_groups(major_b + middle_b)
    assert syms == ["000001", "000002"]


def test_select_quote_symbols_top_by_group_vs_all() -> None:
    """기본 보강 대상은 그룹당 프로브 RS 상위 top_n 합집합; all 은 전 구성주(+라이브 키)."""
    data = {
        "major_categories": [
            {
                "majorCategory": "M",
                "subCategories": [
                    {
                        "middleCategory": "A",
                        "stocks": [
                            {"stockCode": "111110", "stockName": "a1", "marketCap": 100},
                            {"stockCode": "222220", "stockName": "a2", "marketCap": 100},
                        ],
                    },
                    {
                        "middleCategory": "B",
                        "stocks": [
                            {"stockCode": "333330", "stockName": "b1", "marketCap": 100},
                            {"stockCode": "444440", "stockName": "b2", "marketCap": 100},
                        ],
                    },
                ],
            }
        ]
    }
    major_b, middle_b = _build_group_blueprints(data)
    groups = major_b + middle_b
    live = {
        "111110": {"live_signal_score": 1.0, "source_scores": {"return": 10.0}, "source_count": 1},
        "222220": {"live_signal_score": 50.0, "source_scores": {"return": 90.0}, "source_count": 1},
        "333330": {"live_signal_score": 30.0, "source_scores": {"return": 50.0}, "source_count": 1},
        "444440": {"live_signal_score": 40.0, "source_scores": {"return": 60.0}, "source_count": 1},
    }
    top_syms = _select_quote_symbols(
        groups,
        live,
        top_n=1,
        enrichment_mode="top_by_group",
        investor_by_symbol={},
        program_by_symbol={},
    )
    all_syms = _select_quote_symbols(
        groups,
        live,
        top_n=1,
        enrichment_mode="all",
        investor_by_symbol={},
        program_by_symbol={},
    )
    assert set(all_syms) == {"111110", "222220", "333330", "444440"}
    assert set(top_syms) <= set(all_syms)
    assert len(top_syms) == 2


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


def test_classify_theme_quality_matrix() -> None:
    assert classify_theme_quality(None, 10.0) == "데이터부족"
    assert classify_theme_quality(80.0, None) == "데이터부족"
    assert classify_theme_quality(70.0, 70.0) == "주도테마"
    assert classify_theme_quality(70.0, 49.9) == "대장주 편중"
    assert classify_theme_quality(49.9, 70.0) == "단기 순환매"
    assert classify_theme_quality(50.0, 50.0) == "관심테마"
    assert classify_theme_quality(30.0, 30.0) == "약세/제외"


def test_calculate_persistence_score_data_shortage() -> None:
    cur = {
        "date": "2026-05-06",
        "group_type": "major",
        "major_category": "A",
        "middle_category": None,
        "theme_rs": 60.0,
        "theme_rank": 1,
        "total_value_traded": 100.0,
    }
    out = calculate_persistence_score(cur, history_rows=[], group_key=("A", None))
    assert out["persistence_score"] is None
    assert out["rank_top3_days_5d"] == 0


def test_calculate_breadth_score_basic() -> None:
    out = calculate_breadth_score(
        member_count=100,
        rs60_ratio=0.30,
        rs70_ratio=0.10,
        up_ratio=0.70,
        market_up_ratio=0.50,
        value_expansion_ratio=0.25,
    )
    assert out["breadth_score"] is not None
    assert out["relative_up_ratio"] == 0.2
