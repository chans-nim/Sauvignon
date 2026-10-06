from __future__ import annotations
import argparse
import pandas as pd
from src.common.settings import settings
from src.common.logger import get_logger
from src.master.universe_policy import (
    UNSUPPORTED_SYMBOL_ASSET_TYPE,
    classify_daily_asset_type,
)
from src.storage import meta_store
from src.master.lifecycle_policy import apply_lifecycle_overrides

log = get_logger(__name__)
MASTER_DIR = settings.project_root / "data" / "lake" / "master"
OUT_PATH = MASTER_DIR / "universe.parquet"

def load_master(name: str) -> pd.DataFrame:
    path = MASTER_DIR / name
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_parquet(path)

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-spac", action="store_true")
    args = parser.parse_args()

    kospi = load_master("kospi_master.parquet")
    kosdaq = load_master("kosdaq_master.parquet")
    df = pd.concat([kospi, kosdaq], ignore_index=True)
    df["asset_type"] = df["symbol"].map(classify_daily_asset_type)
    df["listing_date"] = pd.to_datetime(df["listing_date"], format="%Y%m%d", errors="coerce")
    df["is_etf"] = False
    df["is_spac"] = df["name"].fillna("").str.contains("스팩", na=False)
    df["is_warning"] = False
    df["is_active"] = True
    df = apply_lifecycle_overrides(df)
    df["updated_at"] = pd.Timestamp.now()
    if not args.include_spac:
        df = df[~df["is_spac"]].copy()
    df = df.drop_duplicates(subset=["symbol"]).sort_values(["market", "symbol"]).reset_index(drop=True)
    df.to_parquet(OUT_PATH, index=False)
    meta_store.ensure_tables()
    meta_store.replace_universe(df)
    log.info("universe saved=%s rows=%s", OUT_PATH, len(df))
    unsupported = df[df["asset_type"] == UNSUPPORTED_SYMBOL_ASSET_TYPE]
    if not unsupported.empty:
        sample = unsupported[["symbol", "name", "market"]].head(10).to_dict("records")
        log.warning(
            "retained but excluded from KIS daily collection: count=%s sample=%s",
            len(unsupported),
            sample,
        )

if __name__ == "__main__":
    main()
