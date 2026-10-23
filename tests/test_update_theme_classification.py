from scripts.update_theme_classification import update


def _stock(code: str, name: str, major: str, middle: str) -> dict:
    return {
        "stockCode": code,
        "stockName": name,
        "marketCap": 1,
        "sectors": [{"majorCategory": major, "middleCategory": middle}],
    }


def test_reviewed_overrides_rebuild_denormalized_categories() -> None:
    data = {
        "stock_to_sectors": [
            _stock("353200", "대덕전자", "반도체", "반도체소재"),
            _stock("000120", "CJ대한통운", "인프라", "유틸리티"),
            _stock("105560", "KB금융", "금융", "은행"),
        ],
        "major_categories": [
            {"majorCategory": "반도체", "subCategories": [{"middleCategory": "반도체소재"}]},
            {"majorCategory": "인프라", "subCategories": [{"middleCategory": "유틸리티"}]},
            {"majorCategory": "금융", "subCategories": [{"middleCategory": "은행"}]},
        ],
    }

    result = update(data)
    paths = {
        stock["stockCode"]: {
            (sector["majorCategory"], sector["middleCategory"])
            for sector in stock["sectors"]
        }
        for stock in result["stock_to_sectors"]
    }

    assert paths["353200"] == {("반도체", "PCB/패키지기판")}
    assert paths["000120"] == {("인프라", "물류")}
    assert paths["105560"] == {("금융", "금융지주")}
    assert result["classification_as_of"] == "2026-10-23"
    assert result["extracted_stock_count"] == 3


def test_reviewed_overrides_are_idempotent() -> None:
    data = {
        "stock_to_sectors": [_stock("036540", "SFA반도체", "반도체", "비메모리/팹리스")],
        "major_categories": [
            {"majorCategory": "반도체", "subCategories": [{"middleCategory": "비메모리/팹리스"}]}
        ],
    }

    once = update(data)
    twice = update(once)

    assert twice == once
