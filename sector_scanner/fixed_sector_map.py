"""Default symbol to sector map and merge with client universe metadata."""

from __future__ import annotations

from typing import Any

DEFAULT_FIXED_SECTOR_MAP: dict[str, dict[str, str]] = {
    "005930": {"sector_code": "001", "sector_name": "Semiconductor"},
    "000660": {"sector_code": "001", "sector_name": "Semiconductor"},
    "373220": {"sector_code": "002", "sector_name": "Battery"},
    "207940": {"sector_code": "003", "sector_name": "Bio"},
    "005380": {"sector_code": "006", "sector_name": "Auto"},
    "035420": {"sector_code": "014", "sector_name": "IT Services"},
    "051910": {"sector_code": "008", "sector_name": "Chemical"},
    "068270": {"sector_code": "013", "sector_name": "Game"},
    "035720": {"sector_code": "012", "sector_name": "Entertainment"},
    "028260": {"sector_code": "004", "sector_name": "Finance"},
}


def symbol_to_sector(symbol: str, fixed: dict[str, dict[str, str]] | None = None) -> dict[str, str] | None:
    m = fixed if fixed is not None else DEFAULT_FIXED_SECTOR_MAP
    key = str(symbol).strip()
    row = m.get(key)
    if not row:
        return None
    sc = row.get("sector_code")
    sn = row.get("sector_name")
    if sc is None or sn is None:
        return None
    return {"sector_code": str(sc).strip(), "sector_name": str(sn).strip()}


def attach_fixed_sector(
    client: Any | None = None,
    *,
    base: dict[str, dict[str, str]] | None = None,
) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    seed = dict(base) if base is not None else dict(DEFAULT_FIXED_SECTOR_MAP)
    for sym, info in seed.items():
        if not isinstance(info, dict):
            continue
        sk = str(sym).strip()
        if not sk:
            continue
        sc = info.get("sector_code")
        sn = info.get("sector_name")
        if sc is None or sn is None:
            continue
        scs, sns = str(sc).strip(), str(sn).strip()
        if not scs or not sns:
            continue
        out[sk] = {"sector_code": scs, "sector_name": sns}

    raw = getattr(client, "_stocks", None) if client is not None else None
    if isinstance(raw, dict):
        for sym, meta in raw.items():
            if not isinstance(meta, dict):
                continue
            sk = str(sym).strip()
            if not sk:
                continue
            sc = meta.get("sector_code")
            sn = meta.get("sector_name")
            if sc is None or sn is None:
                continue
            scs, sns = str(sc).strip(), str(sn).strip()
            if not scs or not sns:
                continue
            out[sk] = {"sector_code": scs, "sector_name": sns}
    return out
