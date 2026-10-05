from __future__ import annotations
from pathlib import Path
from typing import Dict, List
import pandas as pd
from src.common.settings import settings
from src.common.logger import get_logger

log = get_logger(__name__)
RAW_MASTER_DIR = settings.project_root / "data" / "raw" / "master"
OUT_DIR = settings.project_root / "data" / "lake" / "master"
OUT_DIR.mkdir(parents=True, exist_ok=True)
MST_PATH = RAW_MASTER_DIR / "kosdaq_code.mst"
OUT_PATH = OUT_DIR / "kosdaq_master.parquet"
TAIL_LEN = 221  # Official field widths, excluding the line ending.

def parse_row(row: str) -> Dict[str, str]:
    row = row.rstrip("\r\n")
    head = row[:-TAIL_LEN]
    tail = row[-TAIL_LEN:]
    return {"symbol": head[:9].strip(), "std_code": head[9:21].strip(), "name": head[21:].strip(), "market": "KOSDAQ", "raw_tail": tail,
            "is_trading_halt": tail[55:56] == "Y", "is_admin_issue": tail[57:58] == "Y",
            "listing_date": tail[100:108].strip()}

def main() -> None:
    if not MST_PATH.exists():
        raise FileNotFoundError(MST_PATH)
    rows: List[Dict[str, str]] = []
    with MST_PATH.open("r", encoding="cp949") as f:
        for line in f:
            if line.strip():
                rows.append(parse_row(line))
    df = pd.DataFrame(rows)
    df = df[df["symbol"].astype(str).str.len() > 0].copy()
    df.to_parquet(OUT_PATH, index=False)
    log.info("saved %s rows=%s", OUT_PATH, len(df))

if __name__ == "__main__":
    main()
