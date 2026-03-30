from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from src.common.settings import settings

DELTA_DIR = settings.project_root / "data" / "delta"
SNAPSHOT_DIR = settings.project_root / "data" / "snapshot"


def infer_release_type(tag: str) -> str:
    if tag.startswith("data-delta-"):
        return "delta"
    if tag.startswith("data-snapshot-"):
        return "snapshot"
    if tag.startswith("data-full-"):
        return "full"
    raise ValueError(f"unsupported tag format: {tag}")


def manifest_dir_for_tag(tag: str) -> Path:
    release_type = infer_release_type(tag)
    if release_type == "delta":
        return DELTA_DIR
    return SNAPSHOT_DIR


def available_tags(base_dir: Path) -> list[str]:
    return sorted({p.stem for p in base_dir.glob("*.json")})


def resolve_tag(tag: str) -> tuple[str, Path]:
    base_dir = manifest_dir_for_tag(tag)
    manifest_path = base_dir / f"{tag}.json"
    if manifest_path.exists():
        return tag, base_dir

    candidates = [name for name in available_tags(base_dir) if name.startswith(tag)]
    if len(candidates) == 1:
        return candidates[0], base_dir
    if len(candidates) > 1:
        raise FileNotFoundError(f"ambiguous tag '{tag}' in {base_dir}; candidates: {', '.join(candidates)}")

    available = ", ".join(available_tags(base_dir)) or "(none)"
    raise FileNotFoundError(f"missing manifest for tag '{tag}' in {base_dir}; available tags: {available}")


def load_manifest(tag: str) -> tuple[str, dict]:
    resolved_tag, base_dir = resolve_tag(tag)
    path = base_dir / f"{resolved_tag}.json"
    return resolved_tag, json.loads(path.read_text(encoding="utf-8"))


def default_workflow_for(release_type: str) -> str:
    if release_type in {"snapshot", "full"}:
        return "publish-snapshot.yml"
    return "publish-delta.yml"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dispatch GitHub Actions release workflow for delta/full/snapshot asset URLs")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--asset-url", default=None, help="Public asset URL. Naver MYBOX public download URL is allowed.")
    parser.add_argument("--delta-url", default=None, help="Deprecated alias for --asset-url")
    parser.add_argument("--json-url", default=None, help="Optional snapshot manifest JSON URL")
    parser.add_argument("--sha256-url", default=None, help="Optional sha256 URL")
    parser.add_argument("--ticker-state-url", default=None, help="Optional ticker-state parquet URL")
    parser.add_argument("--output1-latest-url", default=None, help="Optional output1-latest parquet URL")
    parser.add_argument("--release-type", choices=("delta", "snapshot", "full"), default=None)
    parser.add_argument("--workflow", default=None, help="Workflow file or name to dispatch")
    parser.add_argument("--run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asset_url = args.asset_url or args.delta_url
    if not asset_url:
        raise ValueError("either --asset-url or --delta-url is required")

    resolved_tag, meta = load_manifest(args.tag)
    release_type = args.release_type or infer_release_type(resolved_tag)
    workflow = args.workflow or default_workflow_for(release_type)

    cmd = [
        "gh", "workflow", "run", workflow,
        "-f", f"tag={resolved_tag}",
        "-f", f"release_type={release_type}",
        "-f", f"asset_url={asset_url}",
        "-f", f"sha256={meta['sha256']}",
        "-f", f"min_date={meta.get('min_date') or ''}",
        "-f", f"max_date={meta.get('max_date') or ''}",
        "-f", f"asset_name={meta['file_name']}",
        "-f", f"row_count={meta.get('row_count') or ''}",
    ]
    optional_inputs = [
        ("json_url", args.json_url),
        ("sha256_url", args.sha256_url),
        ("ticker_state_url", args.ticker_state_url),
        ("output1_latest_url", args.output1_latest_url),
    ]
    for key, value in optional_inputs:
        if value:
            cmd.extend(["-f", f"{key}={value}"])
    print("[COMMAND]")
    print(" ".join(cmd))
    if args.run:
        subprocess.run(cmd, check=True)
        print("[OK] workflow dispatched")
    else:
        print("[DRY-RUN] --run 옵션이 없어 실행하지 않았습니다.")


if __name__ == "__main__":
    main()
