"""Recover missing daily rows only with independent, identity-checked evidence."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from src.storage import meta_store


def classify_stock_status(row: dict, payload: dict, target_date: str, *, today=None) -> str | None:
    info = payload.get("output")
    if not isinstance(info, dict) or str(payload.get("rt_cd", "0")) not in {"0", "0000"}:
        return None
    # A reused short code must never deactivate another listing.
    isin = str(row.get("std_code") or "").strip()
    if not isin or str(info.get("std_pdno") or "").strip() != isin:
        return None
    target = pd.Timestamp(target_date).date()
    market_field = {"KOSPI": "scts", "KOSDAQ": "kosdaq"}.get(row["market"])
    if market_field is None:
        return None
    for field in (f"{market_field}_mket_lstg_abol_dt", "lstg_abol_dt"):
        raw = str(info.get(field) or "").strip()
        if len(raw) != 8 or not raw.isdigit() or raw == "00000000":
            continue
        try:
            effective = datetime.strptime(raw, "%Y%m%d").date()
        except ValueError:
            continue
        listed = pd.to_datetime(info.get(f"{market_field}_mket_lstg_dt"), format="%Y%m%d", errors="coerce")
        if pd.notna(listed) and listed.date() > effective:
            continue
        if effective <= target:
            return "delisted"
    current = today or datetime.now(ZoneInfo("Asia/Seoul")).date()
    if target == current and str(info.get("tr_stop_yn") or "").strip() == "Y":
        return "trading_halt"
    return None


def persist_confirmed_status(row: dict, status: str) -> None:
    """Update the scope shared by gap fill and validation; preserve all prices."""
    column = {"delisted": "is_active", "trading_halt": "is_trading_halt"}[status]
    value = status == "trading_halt"
    con = meta_store.connect()
    try:
        con.execute("BEGIN TRANSACTION")
        matched = con.execute(
            f"UPDATE universe SET {column}=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE symbol=? AND std_code=? AND market=? RETURNING symbol",
            [value, row["symbol"], row["std_code"], row["market"]],
        ).fetchall()
        if len(matched) != 1:
            raise RuntimeError("Stock status recovery did not match exactly one listing")
        # This is a verified exclusion, not a successful price fetch. Keep last_success_date.
        con.execute(
            "UPDATE collect_state SET last_error=NULL, retry_count=0, updated_at=CURRENT_TIMESTAMP "
            "WHERE symbol=? AND timeframe='1d'", [row["symbol"]],
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()
