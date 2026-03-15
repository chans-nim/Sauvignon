from __future__ import annotations
import argparse, json
from datetime import datetime
from pathlib import Path
import duckdb
import pandas as pd
from src.common.settings import settings
from src.common.utils import sha256_file
from src.storage import meta_store
from src.common.logger import get_logger

log = get_logger(__name__)
SILVER_DIR = settings.project_root / "data" / "lake" / "silver" / "ohlcv_daily"
DELTA_DIR = settings.project_root / "data" / "delta"
DELTA_DIR.mkdir(parents=True, exist_ok=True)

def build_tag(ts: datetime | None = None) -> str:
    ts = ts or datetime.now()
    return f"data-delta-{ts.strftime('%Y%m%d')}-{ts.strftime('%H%M')}"

def load_delta_rows(start_date: str, end_date: str) -> pd.DataFrame:
    parquet_files = [p.as_posix() for p in SILVER_DIR.rglob("data.parquet")]
    if not parquet_files:
        return pd.DataFrame(columns=["symbol","market","date","open","high","low","close","volume","value","ingested_at"])
    con = duckdb.connect()
    df = con.execute(
        "SELECT * FROM read_parquet(?) WHERE date BETWEEN ? AND ? ORDER BY market, symbol, date",
        [parquet_files, start_date, end_date]
    ).fetchdf()
    con.close()
    return df

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--tag", default=None)
    args = parser.parse_args()

    meta_store.ensure_tables()
    tag = args.tag or build_tag()
    df = load_delta_rows(args.start_date, args.end_date)
    delta_path = DELTA_DIR / f"{tag}.parquet"
    sha_path = DELTA_DIR / f"{tag}.sha256"
    manifest_path = DELTA_DIR / f"{tag}.json"

    df.to_parquet(delta_path, index=False)
    sha = sha256_file(delta_path)
    sha_path.write_text(sha + "\n", encoding="utf-8")

    manifest = {
        "tag": tag,
        "file_name": delta_path.name,
        "file_path": delta_path.as_posix(),
        "sha256": sha,
        "row_count": int(len(df)),
        "min_date": None if df.empty else str(pd.to_datetime(df["date"]).min().date()),
        "max_date": None if df.empty else str(pd.to_datetime(df["date"]).max().date()),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    meta_store.record_delta(tag, delta_path.name, delta_path.as_posix(), sha, int(len(df)), manifest["min_date"], manifest["max_date"])
    log.info("delta=%s rows=%s", delta_path, len(df))

if __name__ == "__main__":
    main()
