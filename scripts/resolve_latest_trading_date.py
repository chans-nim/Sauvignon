"""Resolve the latest completed trading date shared by liquid KRX anchors."""

from __future__ import annotations

import argparse
from pathlib import Path

from sector_scanner.kis_client import build_kis_client_for_mode


def resolve_latest_trading_date(client, symbols: tuple[str, ...] = ("005930", "000660", "069500")) -> str:
    frame = client.fetch_price_history_frame(list(symbols), days=14)
    if frame is None or bool(getattr(frame, "empty", True)):
        raise RuntimeError("anchor price history is empty")
    common = frame.dropna(axis=0, how="any")
    if bool(getattr(common, "empty", True)):
        raise RuntimeError(f"anchors have no common trading date: {symbols}")
    latest = common.index.max()
    if not hasattr(latest, "date"):
        raise RuntimeError(f"invalid trading-date index: {latest!r}")
    return latest.date().isoformat()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("real", "paper"), default="real")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()

    client = build_kis_client_for_mode(args.mode)
    client.authenticate()
    trading_date = resolve_latest_trading_date(client)
    print(trading_date)
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as fh:
            fh.write(f"trading_date={trading_date}\n")
            fh.write(f"trading_date_compact={trading_date.replace('-', '')}\n")


if __name__ == "__main__":
    main()
