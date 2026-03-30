from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

if __name__ == "__main__" and str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.publish.upload_to_staging import public_url, stage_release_assets


def _run(cmd: list[str]) -> None:
    print("[RUN]", " ".join(cmd))
    subprocess.run(cmd, check=True)


def _run_snapshot_build(publish_tag: str | None) -> dict:
    cmd = [sys.executable, "-m", "src.jobs.full_snapshot_job", "--mode", "snapshot"]
    if publish_tag:
        cmd += ["--tag", publish_tag]
    print("[RUN]", " ".join(cmd))
    p = subprocess.run(cmd, capture_output=True, text=True, check=True)
    out = (p.stdout or "").strip()
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            return json.loads(line)
    print(out)
    raise SystemExit("Could not parse JSON from full_snapshot_job output")


def _dispatch_release(tag: str, urls: dict[str, str]) -> None:
    parquet_name = f"{tag}.parquet"
    asset_url = urls.get(parquet_name)
    if not asset_url:
        raise SystemExit(f"Missing staged parquet URL for {parquet_name}")
    cmd = [
        sys.executable,
        "-m",
        "src.publish.trigger_github_actions",
        "--tag",
        tag,
        "--asset-url",
        asset_url,
        "--run",
    ]
    optional = {
        "--json-url": urls.get(f"{tag}.json"),
        "--sha256-url": urls.get(f"{tag}.sha256"),
        "--ticker-state-url": urls.get(f"{tag}.ticker-state.parquet"),
        "--output1-latest-url": urls.get(f"{tag}.output1-latest.parquet"),
    }
    for flag, value in optional.items():
        if value:
            cmd += [flag, value]
    _run(cmd)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rebuild a local snapshot from the latest GitHub release, then optionally stage and dispatch a new release"
    )
    parser.add_argument("--repo", default="chans-nim/Sauvignon", help="Base release repo (owner/name or URL)")
    parser.add_argument("--base-tag", default=None, help="Optional base release tag to sync before rebuilding")
    parser.add_argument("--publish-tag", default=None, help="Optional new snapshot tag to build locally")
    parser.add_argument("--skip-master-refresh", action="store_true", help="Skip rebuilding meta.duckdb")
    parser.add_argument("--skip-validate", action="store_true", help="Skip local validate_snapshot run")
    parser.add_argument("--stage", action="store_true", help="Copy the built snapshot bundle to staging_dir")
    parser.add_argument(
        "--dispatch-release",
        action="store_true",
        help="After staging, trigger publish-snapshot.yml so GitHub downloads the staged bundle and creates the release",
    )
    args = parser.parse_args()

    if args.dispatch_release:
        args.stage = True

    sync_cmd = [sys.executable, "-m", "scripts.sync_silver_from_github_release", "--repo", args.repo]
    if args.base_tag:
        sync_cmd += ["--tag", args.base_tag]
    _run(sync_cmd)

    if not args.skip_master_refresh:
        _run([sys.executable, "-m", "scripts.run_master_refresh"])

    payload = _run_snapshot_build(args.publish_tag)
    tag = str(payload["tag"])
    print(f"[BUILT] tag={tag}")
    print(f"[BUILT] parquet={payload['parquet_path']}")

    if not args.skip_validate:
        _run(
            [
                sys.executable,
                "-m",
                "scripts.validate_snapshot",
                "--file-path",
                str(payload["parquet_path"]),
                "--allow-missing-meta",
            ]
        )

    urls: dict[str, str] = {}
    if args.stage:
        copied = stage_release_assets(tag)
        urls = {p.name: public_url(p) for p in copied}
        print("[STAGED URLS]")
        for name in sorted(urls):
            print(f"{name}: {urls[name]}")

    if args.dispatch_release:
        _dispatch_release(tag, urls)
        print(f"[DISPATCHED] publish-snapshot.yml for {tag}")


if __name__ == "__main__":
    main()
