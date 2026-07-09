from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.collect_thema_sector_data import (
    _all_unique_stock_symbols_from_groups,
    _build_group_blueprints,
    _build_rows,
    _build_theme_metric_history_rows,
    _build_theme_history_snapshot_rows,
    _combine_theme_history_dirs,
    _build_theme_daily_leader_history_sections,
    _load_theme_history_rows,
    _render_theme_history_calendar_html,
    _theme_history_local_summary,
    classify_theme_quality,
    calculate_breadth_score,
    calculate_persistence_score,
    _score_group_members,
    _select_quote_symbols,
    _render_theme_report_html,
    _resolve_classification_json,
)
from scripts.sync_theme_history_from_github_releases import expand_combined_snapshot_to_daily_files


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


def test_theme_history_local_summary_counts_snapshot_files(tmp_path) -> None:
    (tmp_path / "theme_snapshot_20260510.json").write_text("{}", encoding="utf-8")
    (tmp_path / "theme_snapshot_20260511.json").write_text("{}", encoding="utf-8")
    (tmp_path / "other.txt").write_text("x", encoding="utf-8")
    s = _theme_history_local_summary(tmp_path)
    assert s["file_count"] == 2
    assert s["distinct_file_dates"] == 2
    assert s["min_file_date"] == "2026-05-10"
    assert s["max_file_date"] == "2026-05-11"


def test_load_theme_history_rows_keeps_only_leaders_and_one_stock(tmp_path) -> None:
    payload = {
        "date": "2026-05-12",
        "rows": [
            {
                "date": "2026-05-12",
                "group_type": "middle",
                "major_category": "반도체",
                "middle_category": "메모리",
                "leader_status": "주도",
                "theme_rs": 90.0,
                "theme_rank": 1,
                "leader_top_stocks": [
                    {"symbol": "000660", "name": "SK하이닉스", "rs": 95.0},
                    {"symbol": "005930", "name": "삼성전자", "rs": 90.0},
                ],
            },
            {
                "date": "2026-05-12",
                "group_type": "middle",
                "major_category": "2차전지",
                "middle_category": "셀",
                "leader_status": "관심",
                "theme_rs": 70.0,
                "theme_rank": 2,
                "leader_top_stocks": [{"symbol": "373220", "name": "LG에너지솔루션", "rs": 80.0}],
            },
        ],
    }
    (tmp_path / "theme_snapshot_20260512.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    rows = _load_theme_history_rows(tmp_path, leaders_only=True)

    assert len(rows) == 1
    assert rows[0]["leader_status"] == "주도"
    assert rows[0]["display_path"] == "반도체 > 메모리"
    assert rows[0]["leader_top_stocks"] == [{"symbol": "000660", "name": "SK하이닉스", "rs": 95.0}]


def test_load_theme_history_rows_default_keeps_metric_rows_for_v2(tmp_path) -> None:
    payload = {
        "date": "2026-05-12",
        "rows": [
            {
                "date": "2026-05-12",
                "group_type": "major",
                "major_category": "기계",
                "leader_status": "주도",
                "theme_rs": 80.0,
                "theme_rank": 1,
                "leader_top_stocks": [{"symbol": "090710", "name": "휴림로봇", "rs": 82.8}],
            }
        ],
        "metric_rows": [
            {
                "date": "2026-05-12",
                "group_type": "major",
                "major_category": "기계",
                "leader_status": "관심",
                "theme_rs": 60.1,
                "theme_rank": 1,
            },
            {
                "date": "2026-05-12",
                "group_type": "major",
                "major_category": "자동차",
                "leader_status": "중립",
                "theme_rs": 59.3,
                "theme_rank": 2,
            },
        ],
    }
    (tmp_path / "theme_history_snapshot.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    metric_rows = _load_theme_history_rows(tmp_path)
    leader_rows = _load_theme_history_rows(tmp_path, leaders_only=True)

    assert len(metric_rows) == 2
    assert {r["display_path"] for r in metric_rows} == {"기계", "자동차"}
    assert len(leader_rows) == 1
    assert leader_rows[0]["display_path"] == "기계"


def test_combine_theme_history_dirs_lets_release_override_local(tmp_path) -> None:
    local_dir = tmp_path / "local"
    release_dir = tmp_path / "release"
    dest_dir = tmp_path / "dest"
    local_dir.mkdir()
    release_dir.mkdir()

    (local_dir / "theme_history_snapshot.json").write_text(
        json.dumps(
            {
                "date": "2026-05-11",
                "rows": [
                    {
                        "date": "2026-05-11",
                        "group_type": "major",
                        "major_category": "로봇",
                        "leader_status": "주도",
                        "theme_rs": 80.0,
                        "theme_rank": 1,
                        "leader_top_stocks": [{"symbol": "123456", "name": "로컬", "rs": 80.0}],
                    }
                ],
                "metric_rows": [
                    {
                        "date": "2026-05-11",
                        "group_type": "major",
                        "major_category": "로봇",
                        "leader_status": "관심",
                        "theme_rs": 80.0,
                        "theme_rank": 1,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (release_dir / "theme_history_snapshot.json").write_text(
        json.dumps(
            {
                "date": "2026-05-11",
                "rows": [
                    {
                        "date": "2026-05-11",
                        "group_type": "major",
                        "major_category": "로봇",
                        "leader_status": "주도",
                        "theme_rs": 90.0,
                        "theme_rank": 1,
                        "leader_top_stocks": [{"symbol": "123456", "name": "릴리즈", "rs": 90.0}],
                    }
                ],
                "metric_rows": [
                    {
                        "date": "2026-05-11",
                        "group_type": "major",
                        "major_category": "로봇",
                        "leader_status": "주도",
                        "theme_rs": 90.0,
                        "theme_rank": 1,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = _combine_theme_history_dirs([local_dir, release_dir], dest_dir)
    payload = json.loads((dest_dir / "theme_history_snapshot.json").read_text(encoding="utf-8"))

    assert summary["distinct_dates"] == 1
    assert payload["rows"][0]["theme_rs"] == 90.0
    assert payload["rows"][0]["leader_top_stocks"][0]["name"] == "릴리즈"
    assert payload["metric_rows"][0]["leader_status"] == "주도"


def test_combine_theme_history_dirs_preserves_local_when_release_is_short(tmp_path) -> None:
    local_dir = tmp_path / "local"
    release_dir = tmp_path / "release"
    dest_dir = tmp_path / "dest"
    local_dir.mkdir()
    release_dir.mkdir()

    (local_dir / "theme_snapshot_20260510.json").write_text(
        json.dumps(
            {
                "date": "2026-05-10",
                "rows": [
                    {
                        "date": "2026-05-10",
                        "group_type": "major",
                        "major_category": "기계",
                        "leader_status": "관심",
                        "theme_rs": 60.0,
                        "theme_rank": 2,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (release_dir / "theme_history_snapshot.json").write_text(
        json.dumps(
            {
                "date": "2026-05-11",
                "rows": [
                    {
                        "date": "2026-05-11",
                        "group_type": "major",
                        "major_category": "로봇",
                        "leader_status": "주도",
                        "theme_rs": 90.0,
                        "theme_rank": 1,
                        "leader_top_stocks": [{"symbol": "123456", "name": "대표", "rs": 90.0}],
                    }
                ],
                "metric_rows": [
                    {
                        "date": "2026-05-11",
                        "group_type": "major",
                        "major_category": "로봇",
                        "leader_status": "주도",
                        "theme_rs": 90.0,
                        "theme_rank": 1,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = _combine_theme_history_dirs([local_dir, release_dir], dest_dir)
    metric_rows = _load_theme_history_rows(dest_dir)
    leader_rows = _load_theme_history_rows(dest_dir, leaders_only=True)

    assert summary["distinct_dates"] == 2
    assert {r["date"] for r in metric_rows} == {"2026-05-10", "2026-05-11"}
    assert [r["date"] for r in leader_rows] == ["2026-05-11"]


def test_build_theme_metric_history_rows_keeps_all_groups_for_v2() -> None:
    rows = [
        {
            "group_type": "major",
            "display_path": "기계",
            "major_category": "기계",
            "middle_category": None,
            "analysis": {"leader_status": "관심", "relative_strength_score": 60.1, "relative_strength_rank": 1},
        },
        {
            "group_type": "major",
            "display_path": "자동차",
            "major_category": "자동차",
            "middle_category": None,
            "analysis": {"leader_status": "중립", "relative_strength_score": 59.3, "relative_strength_rank": 2},
        },
    ]

    metric_rows = _build_theme_metric_history_rows(rows, [], "2026-05-12")

    assert len(metric_rows) == 2
    assert [r["leader_status"] for r in metric_rows] == ["관심", "중립"]


def test_build_theme_history_snapshot_rows_falls_back_when_no_strict_leader() -> None:
    rows = [
        {
            "group_type": "major",
            "display_path": "기계",
            "major_category": "기계",
            "middle_category": None,
            "major_stocks": [
                {"symbol": "090710", "name": "휴림로봇", "rs": 82.8},
                {"symbol": "319400", "name": "현대무벡스", "rs": 65.5},
            ],
            "analysis": {"leader_status": "관심", "relative_strength_score": 60.1, "relative_strength_rank": 1},
        },
        {
            "group_type": "major",
            "display_path": "자동차",
            "major_category": "자동차",
            "middle_category": None,
            "major_stocks": [{"symbol": "005380", "name": "현대차", "rs": 70.0}],
            "analysis": {"leader_status": "중립", "relative_strength_score": 59.3, "relative_strength_rank": 2},
        },
    ]

    snapshot_rows = _build_theme_history_snapshot_rows(rows, [], "2026-05-12", fallback_count=1)

    assert len(snapshot_rows) == 1
    assert snapshot_rows[0]["display_path"] == "기계"
    assert snapshot_rows[0]["leader_status"] == "주도"
    assert snapshot_rows[0]["source_leader_status"] == "관심"
    assert snapshot_rows[0]["leader_top_stocks"] == [{"symbol": "090710", "name": "휴림로봇", "rs": 82.8}]


def test_publish_combined_snapshot_keeps_leaders_only(tmp_path) -> None:
    from scripts.publish_theme_sector_release import write_combined_snapshot

    (tmp_path / "theme_snapshot_20260511.json").write_text(
        json.dumps(
            {
                "date": "2026-05-11",
                "rows": [
                    {
                        "date": "2026-05-11",
                        "group_type": "major",
                        "major_category": "로봇",
                        "leader_status": "주도",
                        "theme_rs": 88.0,
                        "theme_rank": 1,
                        "leader_top_stocks": [
                            {"symbol": "123456", "name": "대표", "rs": 91.0},
                            {"symbol": "654321", "name": "차순위", "rs": 82.0},
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "theme_snapshot_20260512.json").write_text(
        json.dumps(
            {
                "date": "2026-05-12",
                "rows": [
                    {
                        "date": "2026-05-12",
                        "group_type": "major",
                        "major_category": "중립테마",
                        "leader_status": "중립",
                        "theme_rs": 55.0,
                        "theme_rank": 3,
                        "leader_top_stocks": [{"symbol": "111111", "name": "제외", "rs": 70.0}],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    out_path = tmp_path / "theme_history_snapshot.json"

    write_combined_snapshot(tmp_path, "2026-05-12", out_path)
    payload = json.loads(out_path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 2
    assert payload["date"] == "2026-05-12"
    assert len(payload["rows"]) == 1
    assert payload["rows"][0]["leader_status"] == "주도"
    assert payload["rows"][0]["leader_top_stocks"] == [{"symbol": "123456", "name": "대표", "rs": 91.0}]
    assert len(payload["metric_rows"]) == 2


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


def test_load_theme_history_rows_reads_combined_and_daily_snapshots(tmp_path) -> None:
    (tmp_path / "theme_history_snapshot.json").write_text(
        json.dumps(
            {
                "date": "2026-06-09",
                "rows": [
                    {
                        "date": "2026-06-09",
                        "group_type": "major",
                        "major_category": "Old",
                        "leader_status": "二쇰룄",
                        "theme_rs": 70.0,
                        "theme_rank": 1,
                    }
                ],
                "metric_rows": [
                    {
                        "date": "2026-06-09",
                        "group_type": "major",
                        "major_category": "Old",
                        "leader_status": "二쇰룄",
                        "theme_rs": 70.0,
                        "theme_rank": 1,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "theme_snapshot_20260708.json").write_text(
        json.dumps(
            {
                "date": "2026-07-08",
                "rows": [
                    {
                        "date": "2026-07-08",
                        "group_type": "major",
                        "major_category": "Recent",
                        "leader_status": "二쇰룄",
                        "theme_rs": 80.0,
                        "theme_rank": 1,
                    }
                ],
                "metric_rows": [
                    {
                        "date": "2026-07-08",
                        "group_type": "major",
                        "major_category": "Recent",
                        "leader_status": "二쇰룄",
                        "theme_rs": 80.0,
                        "theme_rank": 1,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    rows = _load_theme_history_rows(tmp_path)

    assert {r["date"] for r in rows} == {"2026-06-09", "2026-07-08"}


def test_expand_combined_snapshot_to_daily_files(tmp_path) -> None:
    combined = tmp_path / "theme_history_snapshot.json"
    combined.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "date": "2026-07-09",
                "rows": [
                    {"date": "2026-07-08", "group_type": "major", "major_category": "A", "leader_status": "二쇰룄"},
                    {"date": "2026-07-09", "group_type": "major", "major_category": "B", "leader_status": "二쇰룄"},
                ],
                "metric_rows": [
                    {"date": "2026-07-08", "group_type": "major", "major_category": "A", "leader_status": "二쇰룄"},
                    {"date": "2026-07-09", "group_type": "major", "major_category": "B", "leader_status": "二쇰룄"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    dest = tmp_path / "dest"

    written = expand_combined_snapshot_to_daily_files(combined, dest)

    assert {p.name for p in written} == {"theme_snapshot_20260708.json", "theme_snapshot_20260709.json"}
    payload = json.loads((dest / "theme_snapshot_20260709.json").read_text(encoding="utf-8"))
    assert payload["date"] == "2026-07-09"
    assert payload["rows"][0]["major_category"] == "B"
    assert payload["metric_rows"][0]["major_category"] == "B"
