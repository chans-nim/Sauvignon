"""Validate that release assets contain a complete snapshot for one trading date."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def validate_release_assets(directory: Path, trading_date: str, expected_tag: str) -> None:
    def load(name: str) -> dict:
        path = directory / name
        if not path.is_file():
            raise ValueError(f"missing release asset: {name}")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"invalid JSON object: {name}")
        return value

    overview = load("overview.json")
    index = load("theme_history_index.json")
    snapshot = load("theme_history_snapshot.json")
    meta = overview.get("metadata") or {}
    quality = meta.get("market_data_quality") or {}

    checks = {
        "overview trading date": meta.get("analysis_trading_date") == trading_date,
        "overview snapshot date": meta.get("theme_snapshot_date") == trading_date,
        "recommendation validity": meta.get("recommendation_data_valid") is True,
        "market-data validity": quality.get("valid") is True,
        "nonempty expected universe": int(quality.get("expected_count") or 0) > 0,
        "index trading date": index.get("snapshot_date") == trading_date,
        "index release tag": index.get("release_tag") == expected_tag,
        "snapshot trading date": snapshot.get("date") == trading_date,
        "snapshot metric rows": bool(snapshot.get("metric_rows")),
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise ValueError("release validation failed: " + ", ".join(failed))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--trading-date", required=True)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    validate_release_assets(args.dir, args.trading_date, args.tag)
    print(f"validated {args.tag} for {args.trading_date}")


if __name__ == "__main__":
    main()
