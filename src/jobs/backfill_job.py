from __future__ import annotations
import argparse
from datetime import datetime
from src.storage import meta_store
from src.collect.collect_backfill import run_backfill
from src.common.logger import get_logger
from src.common.settings import settings

log = get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    log.info("backfill_chunk_days=%s (env BACKFILL_CHUNK_DAYS)", settings.backfill_chunk_days)
    meta_store.ensure_tables()
    universe = meta_store.load_universe(limit=args.limit)
    run_id = f"backfill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    success, failed = run_backfill(universe.to_dict(orient="records"), args.start_date, args.end_date)
    note = f"{args.start_date}..{args.end_date}"
    meta_store.log_run(run_id, "backfill_job", "success" if failed == 0 else "partial", len(universe), success, failed, note)
    log.info("backfill finished total=%s success=%s failed=%s", len(universe), success, failed)


if __name__ == "__main__":
    main()
