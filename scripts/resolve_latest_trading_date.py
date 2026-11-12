"""Resolve the latest completed trading date shared by liquid KRX anchors."""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from sector_scanner.kis_client import build_kis_client_for_mode


def resolve_latest_trading_date(
    client,
    symbols: tuple[str, ...] = ("005930", "000660", "069500"),
    *,
    now: dt.datetime | None = None,
) -> str:
    frame = client.fetch_price_history_frame(list(symbols), days=14)
    if frame is None or bool(getattr(frame, "empty", True)):
        raise RuntimeError("anchor price history is empty")
    current = now or dt.datetime.now(ZoneInfo("Asia/Seoul"))
    current = current.astimezone(ZoneInfo("Asia/Seoul")) if current.tzinfo else current.replace(tzinfo=ZoneInfo("Asia/Seoul"))
    # KIS can expose today's reference-price row before the session is final,
    # with zero volume/value.  Theme reports require the completed integrated
    # KRX/NXT session, so today's row becomes eligible only after 20:00 KST.
    cutoff = current.date() if current.time() >= dt.time(20, 0) else current.date() - dt.timedelta(days=1)
    eligible = frame[pd.to_datetime(frame.index).date <= cutoff]
    common = eligible.dropna(axis=0, how="any")
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
