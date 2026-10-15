from __future__ import annotations
import argparse
import json
import time
from datetime import datetime
import pandas as pd
from src.clients.kis_auth import get_client
from src.collect.base_collect import fetch_ohlcv_chunked, validate_ohlcv
from src.storage import meta_store, parquet_store
from src.common.logger import get_logger
from src.common.settings import settings
from src.collect.status_recovery import classify_stock_status, persist_confirmed_status

log = get_logger(__name__)

_client = get_client()


def _collect_row(row: dict, start_date: str, end_date: str) -> tuple[bool, str | None]:
    symbol = str(row["symbol"])
    market = str(row["market"])
    log.info("collect %s %s %s", market, symbol, row["name"])
    try:
        existing = parquet_store.load_symbol_range(symbol, market, start_date, end_date)
        combined, raw_payloads, skipped_with_ratio = fetch_ohlcv_chunked(
            _client,
            symbol,
            market,
            start_date,
            end_date,
            chunk_days=settings.backfill_chunk_days,
            existing_df=existing if len(existing) > 0 else None,
            coverage_threshold=0.7,
            force_fetch_end_date=True,
        )
        for c_start, c_end, ratio in skipped_with_ratio:
            if ratio < 0.9:
                log.warning(
                    "collect_daily skipped chunk low coverage %s %s..%s ratio=%.3f",
                    symbol,
                    c_start,
                    c_end,
                    ratio,
                )
        for c_start, _c_end, payload in raw_payloads:
            parquet_store.save_raw_json(symbol, payload, suffix=c_start.replace("-", ""))
        df = validate_ohlcv(combined)
        target_date = pd.Timestamp(end_date).normalize()
        has_target = not df.empty and (pd.to_datetime(df["date"]).dt.normalize() == target_date).any()
        if not has_target:
            target_rows = combined.loc[pd.to_datetime(combined["date"]).dt.normalize() == target_date]
            sample = target_rows[["date", "open", "high", "low", "close", "volume", "value"]].head(3).to_dict("records")
            raise RuntimeError(
                f"KIS response has no valid row for required target date {end_date}; "
                f"received_rows={len(combined)} target_rows={len(target_rows)} target_sample={sample}"
            )
        saved = parquet_store.upsert_ohlcv_from_df(df)
        last_date = df["date"].max().strftime("%Y-%m-%d")
        meta_store.upsert_collect_state(symbol, True, last_date, None)
        log.info("rows=%s files=%s last_date=%s", len(df), len(saved), last_date)
        return True, None
    except Exception as exc:
        meta_store.upsert_collect_state(symbol, False, None, str(exc))
        log.error("collect failed %s: %s", symbol, exc)
        return False, str(exc)


def collect_rows(rows, start_date: str, end_date: str, *, max_attempts: int = 3,
                 report: list[dict] | None = None, sleep_fn=time.sleep) -> tuple[int, int]:
    """Collect every row and retry only failed/empty symbols.

    The returned handled count includes price successes and independently verified
    lifecycle exclusions. Unexplained missing rows remain failures; no prices are fabricated.
    """
    pending = [dict(row) for row in rows]
    succeeded: set[str] = set()
    errors: dict[str, str] = {}
    attempts = max(1, int(max_attempts))
    for attempt in range(1, attempts + 1):
        if not pending:
            break
        if attempt > 1:
            log.warning("retrying %s failed/empty symbol(s), attempt=%s/%s", len(pending), attempt, attempts)
            sleep_fn(min(30, 2 ** (attempt - 1)))
        retry_rows: list[dict] = []
        for row in pending:
            ok, _err = _collect_row(row, start_date, end_date)
            if ok:
                succeeded.add(str(row["symbol"]))
            else:
                errors[str(row["symbol"])] = str(_err)
                retry_rows.append(row)
        pending = retry_rows
    unresolved = []
    for row in pending:
        symbol = str(row["symbol"])
        entry = {"symbol": symbol, "market": row["market"], "target_date": end_date,
                 "error": errors[symbol], "outcome": "failed"}
        # Network/auth/validation errors must not become lifecycle exclusions.
        if (errors[symbol].startswith("KIS response has no valid row")
                and " target_rows=0 " in errors[symbol]):
            try:
                payload = _client.get_stock_info(symbol)
                entry["stock_info"] = payload
                status = classify_stock_status(row, payload, end_date)
                if status:
                    persist_confirmed_status(row, status)
                    entry["outcome"] = status
                    succeeded.add(symbol)
                    log.warning("verified collection exclusion %s: %s target=%s", symbol, status, end_date)
            except Exception as exc:
                entry["recovery_error"] = str(exc)
                log.warning("status recovery failed %s: %s", symbol, exc)
        if entry["outcome"] == "failed":
            unresolved.append(row)
        if report is not None:
            report.append(entry)
    return len(succeeded), len(unresolved)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    meta_store.ensure_tables()
    universe_df = meta_store.load_universe(limit=args.limit, target_date=args.end_date)
    run_id = f"collect_daily_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    report: list[dict] = []
    success, failed = collect_rows(universe_df.to_dict(orient="records"), args.start_date, args.end_date, report=report)
    excluded = sum(item["outcome"] != "failed" for item in report)
    report_path = settings.project_root / "data" / "reports" / "collection" / f"{run_id}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps({"start_date": args.start_date, "end_date": args.end_date,
        "price_success": success - excluded, "verified_exclusions": excluded, "failed": failed,
        "diagnostics": report}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    meta_store.log_run(run_id, "collect_daily", "success" if failed == 0 else "partial", len(universe_df), success, failed,
                       note=f"verified_exclusions={excluded}; report={report_path}")
    if failed:
        raise SystemExit(f"collect_daily incomplete: success={success} failed={failed}; refusing downstream publish")


if __name__ == "__main__":
    main()
