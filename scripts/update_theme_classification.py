"""Apply reviewed taxonomy corrections and rebuild the denormalized theme JSON.

The canonical source is ``stock_to_sectors``. ``major_categories`` is regenerated
for compatibility with older readers; do not edit both structures by hand.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data" / "theme" / "theme_major_middle_stock_classification_dup_allowed.json"

PATH_RENAMES = {
    ("바이오", "CDMO"): ("바이오", "CRO/CDMO"),
    ("반도체", "테스트소켓"): ("반도체", "테스트/인터페이스"),
}

# 사업영역이 둘 이상의 기존 테마에 실질적으로 걸치는 대표 종목.
# 과도한 테마 편입을 막기 위해 공식 사업영역이 명확한 경우만 추가한다.
ADDITIONAL_SECTORS: dict[str, list[tuple[str, str]]] = {
    "005930": [("반도체", "비메모리/팹리스")],               # 삼성전자: 메모리 + 시스템LSI/파운드리
    "034020": [("기계", "산업기계")],                      # 두산에너빌리티: 원전 + 발전/산업기계
    "012450": [("방산", "우주항공")],                      # 한화에어로스페이스
    "079550": [("방산", "우주항공")],                      # LIG넥스원
    "086520": [("지주사", "지주사")],                      # 에코프로
    "051910": [("2차전지", "음극재/소재")],                # LG화학: 석유화학 + 전지소재
    "035420": [("IT/플랫폼", "SW/AI")],                    # NAVER
    "035720": [("IT/플랫폼", "SW/AI")],                    # 카카오
}

# 대형 이차전지 셀 업체와 다른 사업모델을 분리한다.
OTHER_BATTERY_CODES = {"082920", "091580", "004490", "126730"}


def _sector_tuple(value: dict[str, Any]) -> tuple[str, str]:
    return str(value.get("majorCategory") or "").strip(), str(value.get("middleCategory") or "").strip()


def update(data: dict[str, Any]) -> dict[str, Any]:
    stocks = [x for x in (data.get("stock_to_sectors") or []) if isinstance(x, dict)]
    for stock in stocks:
        code = str(stock.get("stockCode") or "").zfill(6)
        paths: list[tuple[str, str]] = []
        for sector in (stock.get("sectors") or []):
            if not isinstance(sector, dict):
                continue
            path = PATH_RENAMES.get(_sector_tuple(sector), _sector_tuple(sector))
            if code in OTHER_BATTERY_CODES and path == ("2차전지", "배터리셀"):
                path = ("2차전지", "기타전지/부품")
            if path not in paths:
                paths.append(path)
        for path in ADDITIONAL_SECTORS.get(code, []):
            if path not in paths:
                paths.append(path)
        stock["sectors"] = [{"majorCategory": major, "middleCategory": middle} for major, middle in paths]

    old_majors = [x for x in (data.get("major_categories") or []) if isinstance(x, dict)]
    ordered_paths: list[tuple[str, str]] = []
    major_order: list[str] = []
    for major_item in old_majors:
        major = str(major_item.get("majorCategory") or "").strip()
        if major not in major_order:
            major_order.append(major)
        for sub in (major_item.get("subCategories") or []):
            if not isinstance(sub, dict):
                continue
            path = PATH_RENAMES.get((major, str(sub.get("middleCategory") or "").strip()), (major, str(sub.get("middleCategory") or "").strip()))
            if path not in ordered_paths:
                ordered_paths.append(path)
            if path == ("2차전지", "배터리셀") and ("2차전지", "기타전지/부품") not in ordered_paths:
                ordered_paths.append(("2차전지", "기타전지/부품"))

    by_path: dict[tuple[str, str], list[dict[str, Any]]] = {path: [] for path in ordered_paths}
    for stock in stocks:
        for sector in stock.get("sectors") or []:
            path = _sector_tuple(sector)
            by_path.setdefault(path, []).append(stock)
            if path[0] not in major_order:
                major_order.append(path[0])

    majors: list[dict[str, Any]] = []
    for major in major_order:
        subs = []
        for path, members in by_path.items():
            if path[0] != major:
                continue
            members = sorted(members, key=lambda s: (-float(s.get("marketCap") or 0), str(s.get("stockName") or ""), str(s.get("stockCode") or "")))
            subs.append({"middleCategory": path[1], "count": len(members), "stocks": members})
        majors.append({"majorCategory": major, "count": sum(x["count"] for x in subs), "subCategories": subs})

    assignment_count = sum(len(s.get("sectors") or []) for s in stocks)
    multi_count = sum(1 for s in stocks if len(s.get("sectors") or []) > 1)
    data["schema_version"] = "2.1"
    data["description"] = "복수 테마 허용 분류. stock_to_sectors가 정본이며 major_categories는 호환용 파생 데이터." 
    data["classification_as_of"] = "2026-09-22"
    data["classification_method"] = "사업영역 기반 주분류 + 검증된 복수 테마; 광범위한 단순 수혜주는 제외"
    data["classification_notes"] = [
        "CRO와 CDMO, 반도체 테스트 서비스와 인터페이스 부품은 각각 포괄 중분류로 명시",
        "리튬일차전지·납축전지·배터리부품은 배터리셀에서 기타전지/부품으로 분리",
        "시가총액·PER 필드는 원본 스냅샷 값이며 실시간 기초정보로 간주하지 않음",
    ]
    data["classification_sources"] = [
        "상장사 공식 홈페이지/IR 및 KRX KIND 공시",
        "한국거래소 종목코드와 수집 시세 응답 교차검증",
    ]
    data["major_categories"] = majors
    data["stock_to_sectors"] = stocks
    data["extracted_stock_count"] = assignment_count
    data["unique_stock_count"] = len({str(s.get("stockCode") or "").zfill(6) for s in stocks})
    data["actual_multi_sector_stock_count_in_source"] = multi_count
    data["major_category_count"] = len(majors)
    data["middle_category_count"] = sum(len(x["subCategories"]) for x in majors)
    return data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args()
    data = json.loads(args.path.read_text(encoding="utf-8"))
    args.path.write_text(json.dumps(update(data), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
