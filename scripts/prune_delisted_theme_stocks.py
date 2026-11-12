"""Remove codes absent from the official KIS KOSPI/KOSDAQ/KONEX masters."""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import zipfile
from pathlib import Path

import requests


MASTER_URLS = {
    "KOSPI": "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip",
    "KOSDAQ": "https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip",
    "KONEX": "https://new.real.download.dws.co.kr/common/master/konex_code.mst.zip",
}


def fetch_listed_codes() -> set[str]:
    codes: set[str] = set()
    for url in MASTER_URLS.values():
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = archive.namelist()
            if len(names) != 1:
                raise RuntimeError(f"unexpected master archive contents: {url} {names}")
            lines = archive.read(names[0]).decode("cp949").splitlines()
        codes.update(line[:9].strip() for line in lines if line[:9].strip())
    return codes


def classification_codes(data: dict) -> set[str]:
    return {
        str(stock.get("stockCode") or "").strip().zfill(6)
        for major in data.get("major_categories") or []
        for middle in major.get("subCategories") or []
        for stock in middle.get("stocks") or []
        if str(stock.get("stockCode") or "").strip()
    }


def prune_unlisted(data: dict, listed_codes: set[str], *, as_of: str) -> list[str]:
    removed = sorted(classification_codes(data) - listed_codes)
    removed_set = set(removed)
    if not removed:
        return []

    majors: list[dict] = []
    for major in data.get("major_categories") or []:
        middles: list[dict] = []
        for middle in major.get("subCategories") or []:
            stocks = [
                stock
                for stock in middle.get("stocks") or []
                if str(stock.get("stockCode") or "").strip().zfill(6) not in removed_set
            ]
            if not stocks:
                continue
            middle["stocks"] = stocks
            middle["count"] = len(stocks)
            middles.append(middle)
        if not middles:
            continue
        major["subCategories"] = middles
        major["count"] = sum(len(middle["stocks"]) for middle in middles)
        majors.append(major)
    data["major_categories"] = majors
    data["stock_to_sectors"] = [
        stock
        for stock in data.get("stock_to_sectors") or []
        if str(stock.get("stockCode") or "").strip().zfill(6) not in removed_set
    ]

    occurrence_count = sum(
        len(middle["stocks"])
        for major in majors
        for middle in major["subCategories"]
    )
    remaining_codes = classification_codes(data)
    data["extracted_stock_count"] = occurrence_count
    data["unique_stock_count"] = len(remaining_codes)
    data["actual_multi_sector_stock_count_in_source"] = sum(
        1 for stock in data["stock_to_sectors"] if len(stock.get("sectors") or []) > 1
    )
    data["major_category_count"] = len(majors)
    data["middle_category_count"] = sum(len(major["subCategories"]) for major in majors)
    data["classification_as_of"] = as_of
    data["listing_validation"] = {
        "as_of": as_of,
        "source": "KIS official KOSPI/KOSDAQ/KONEX master files",
        "source_urls": list(MASTER_URLS.values()),
        "removed_unlisted_codes": removed,
    }
    return removed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--classification-json",
        type=Path,
        default=Path("data/theme/theme_major_middle_stock_classification_dup_allowed.json"),
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    data = json.loads(args.classification_json.read_text(encoding="utf-8"))
    removed = prune_unlisted(data, fetch_listed_codes(), as_of=dt.date.today().isoformat())
    print(f"unlisted={len(removed)} codes={removed}")
    if args.apply and removed:
        args.classification_json.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"updated {args.classification_json}")


if __name__ == "__main__":
    main()
