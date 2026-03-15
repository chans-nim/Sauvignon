from __future__ import annotations

import argparse
import json
from datetime import datetime

from src.common.logger import get_logger
from src.transform.build_snapshot import build_snapshot

log = get_logger(__name__)


def build_full_tag(ts: datetime | None = None) -> str:
    ts = ts or datetime.now()
    return f"data-full-{ts.strftime('%Y%m%d')}"


def build_compacted_snapshot_tag(ts: datetime | None = None) -> str:
    ts = ts or datetime.now()
    return f"data-snapshot-{ts.strftime('%Y%m%d')}-{ts.strftime('%H%M')}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build canonical full/snapshot parquet from current silver")
    parser.add_argument(
        "--mode",
        choices=("full", "snapshot"),
        default="snapshot",
        help="full=initial full snapshot, snapshot=periodic compacted snapshot",
    )
    parser.add_argument("--tag", default=None, help="Override generated tag")
    args = parser.parse_args()

    now = datetime.now()
    if args.mode == "full":
        tag = args.tag or build_full_tag(now)
        release_type = "full"
    else:
        tag = args.tag or build_compacted_snapshot_tag(now)
        release_type = "snapshot"

    result = build_snapshot(tag=tag, release_type=release_type)
    log.info(
        "snapshot built tag=%s type=%s rows=%s path=%s",
        result.tag,
        result.release_type,
        result.row_count,
        result.file_path,
    )
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
