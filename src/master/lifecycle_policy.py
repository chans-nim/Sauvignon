from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from src.common.logger import get_logger

log = get_logger(__name__)
OVERRIDES_PATH = Path(__file__).with_name("lifecycle_overrides.json")


def apply_lifecycle_overrides(
    universe: pd.DataFrame, *, as_of: date | None = None, overrides_path: Path = OVERRIDES_PATH
) -> pd.DataFrame:
    """Apply sourced delisting events when a vendor master still retains a listing.

    Match the market and ISIN as well as the short code to avoid excluding a
    different listing that later reuses a code. Historical silver is untouched.
    """
    current = as_of or datetime.now(ZoneInfo("Asia/Seoul")).date()
    events = json.loads(overrides_path.read_text(encoding="utf-8"))
    result = universe.copy()
    for event in events:
        effective = date.fromisoformat(event["delisting_date"])
        if not event.get("source_url"):
            raise ValueError("Lifecycle event requires a source URL")
        if current < effective:
            continue
        mask = (
            result["symbol"].eq(event["symbol"])
            & result["std_code"].eq(event["std_code"])
            & result["market"].eq(event["market"])
        )
        if mask.any():
            result.loc[mask, "is_active"] = False
            log.warning("inactive delisted listing %s effective=%s source=%s",
                        event["symbol"], effective, event["source_url"])
    return result
