"""
다운로드한 스냅샷 파일(data/snapshot/{tag}.parquet, .json, .sha256)을 베이스로
로컬 data_manifest.json 을 갱신하고, GitHub Actions 릴리즈 워크플로를 트리거합니다.

사용 순서 (프로젝트 루트에서):
  1) Drive에서 3종 다운로드:
     python -m scripts.download_snapshot_from_drive --tag data-snapshot-20260317-1338 ...
  2) 로컬 manifest 반영 후 릴리즈 트리거 (parquet Drive URL 필요):
     python -m scripts.release_snapshot_from_drive --tag data-snapshot-20260317-1338 ^
       --parquet-url "https://drive.google.com/file/d/.../view?usp=sharing" --update-manifest --run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

if __name__ == "__main__" and str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common.settings import settings
from src.publish.manifest_update import (
    build_release_entry,
    ensure_manifest_structure,
    update_manifest_payload,
)
from src.publish.trigger_github_actions import (
    default_workflow_for,
    infer_release_type,
    load_manifest,
)

SNAPSHOT_DIR = settings.project_root / "data" / "snapshot"
DATA_MANIFEST_PATH = settings.project_root / "data_manifest.json"


def _verify_sha256(parquet_path: Path, expected_hex: str) -> bool:
    h = hashlib.sha256()
    with open(parquet_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest() == expected_hex.strip().lower()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update data_manifest from downloaded snapshot and/or trigger GitHub Release workflow"
    )
    default_tag = "data-snapshot-20260317-1338"
    default_parquet_url = "https://drive.google.com/file/d/1kWG4xA5Sc-_0I41Ut53SW7StVAHHXo7u/view?usp=sharing"
    parser.add_argument("--tag", default=default_tag, help=f"Snapshot tag (default: {default_tag})")
    parser.add_argument(
        "--parquet-url",
        default=default_parquet_url,
        help="Google Drive (or public) URL for the parquet file (used by the workflow to download)",
    )
    parser.add_argument(
        "--update-manifest",
        action="store_true",
        default=True,
        help="Update repo data_manifest.json from data/snapshot/{tag}.json and local parquet (default: True)",
    )
    parser.add_argument(
        "--no-update-manifest",
        action="store_false",
        dest="update_manifest",
        help="Disable updating data_manifest.json",
    )
    parser.add_argument(
        "--verify-sha256",
        action="store_true",
        default=True,
        help="Verify local parquet sha256 against .sha256 file before proceeding (default: True)",
    )
    parser.add_argument(
        "--no-verify-sha256",
        action="store_false",
        dest="verify_sha256",
        help="Skip sha256 verification",
    )
    parser.add_argument("--run", action="store_true", help="Actually run gh workflow (default: dry-run)")
    args = parser.parse_args()

    tag = args.tag.rstrip("/")
    json_path = SNAPSHOT_DIR / f"{tag}.json"
    parquet_path = SNAPSHOT_DIR / f"{tag}.parquet"
    sha_path = SNAPSHOT_DIR / f"{tag}.sha256"

    if not json_path.exists():
        print(f"Missing {json_path}. Run download_snapshot_from_drive first.")
        sys.exit(1)
    if not parquet_path.exists():
        print(f"Missing {parquet_path}. Run download_snapshot_from_drive first.")
        sys.exit(1)

    manifest = json.loads(json_path.read_text(encoding="utf-8"))
    file_name = manifest.get("file_name") or f"{tag}.parquet"
    sha256_value = manifest.get("sha256") or ""
    if not sha256_value and sha_path.exists():
        sha256_value = sha_path.read_text(encoding="utf-8").split()[0].strip()
    if not sha256_value:
        print("Could not determine sha256 from .json or .sha256 file.")
        sys.exit(1)

    if args.verify_sha256:
        print("Verifying local parquet sha256...")
        if not _verify_sha256(parquet_path, sha256_value):
            print("sha256 mismatch. Aborting.")
            sys.exit(1)
        print("sha256 OK.")

    if args.update_manifest:
        print("Updating data_manifest.json from downloaded snapshot...")
        existing = {}
        if DATA_MANIFEST_PATH.exists():
            existing = json.loads(DATA_MANIFEST_PATH.read_text(encoding="utf-8"))
        bytes_size = parquet_path.stat().st_size
        release_type = manifest.get("release_type") or infer_release_type(tag)
        entry = build_release_entry(
            tag=tag,
            release_type=release_type,
            file_name=file_name,
            sha256=sha256_value,
            bytes_size=bytes_size,
            created_at=manifest.get("created_at") or "",
            row_count=manifest.get("row_count"),
            min_date=manifest.get("min_date"),
            max_date=manifest.get("max_date"),
        )
        out = update_manifest_payload(existing, entry)
        DATA_MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        DATA_MANIFEST_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote {DATA_MANIFEST_PATH}")

    # Dispatch workflow (trigger_github_actions logic)
    resolved_tag, meta = load_manifest(tag)
    release_type = infer_release_type(resolved_tag)
    workflow = default_workflow_for(release_type)
    cmd = [
        "gh",
        "workflow",
        "run",
        workflow,
        "-f", f"tag={resolved_tag}",
        "-f", f"release_type={release_type}",
        "-f", f"asset_url={args.parquet_url}",
        "-f", f"sha256={meta['sha256']}",
        "-f", f"min_date={meta.get('min_date') or ''}",
        "-f", f"max_date={meta.get('max_date') or ''}",
        "-f", f"asset_name={meta.get('file_name') or file_name}",
        "-f", f"row_count={meta.get('row_count') or ''}",
    ]
    print("[COMMAND]")
    print(" ".join(cmd))
    if args.run:
        subprocess.run(cmd, check=True)
        print("[OK] workflow dispatched")
    else:
        print("[DRY-RUN] Add --run to actually dispatch the workflow.")


if __name__ == "__main__":
    main()
