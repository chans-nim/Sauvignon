from __future__ import annotations

import re
from typing import Any


# KIS domestic daily-price APIs use six-character KRX short codes, including
# newer codes containing letters (for example 0007C0). The KRX
# master files also contain longer product codes (for example, ELW-style
# entries beginning with Q); keeping those rows in the universe is useful for
# auditability, but sending them to the daily stock endpoint can never produce
# the required daily row.
KIS_DAILY_SYMBOL_PATTERN = r"[0-9][0-9A-Z]{5}"
KIS_DAILY_SYMBOL_RE = re.compile(rf"^{KIS_DAILY_SYMBOL_PATTERN}$")
COLLECTIBLE_ASSET_TYPE = "stock"
UNSUPPORTED_SYMBOL_ASSET_TYPE = "unsupported_symbol"


def is_kis_daily_collectible_symbol(value: Any) -> bool:
    return bool(KIS_DAILY_SYMBOL_RE.fullmatch(str(value).strip()))


def classify_daily_asset_type(value: Any) -> str:
    if is_kis_daily_collectible_symbol(value):
        return COLLECTIBLE_ASSET_TYPE
    return UNSUPPORTED_SYMBOL_ASSET_TYPE


def collectible_universe_sql(alias: str | None = None) -> str:
    """Return the shared DuckDB predicate for the KIS daily collection scope."""
    prefix = f"{alias}." if alias else ""
    return (
        f"{prefix}is_active = TRUE "
        f"AND COALESCE({prefix}is_trading_halt, FALSE) = FALSE "
        f"AND regexp_matches(TRIM({prefix}symbol), '^{KIS_DAILY_SYMBOL_PATTERN}$')"
    )
