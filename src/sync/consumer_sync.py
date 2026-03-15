from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import pandas as pd

from src.common.settings import settings
from src.common.utils import sha256_file
from src.storage import parquet_store
from src.sync.github_release_client import GitHubReleaseClient, ReleaseAsset, ReleaseInfo

MANIFEST_PATH = settings.project_root / "data_manifest.json"
APPLIED_STATE_PATH = settings.project_root / "meta" / "applied_release_state.json"
DOWNLOAD_DIR = settings.project_root / "data" / "downloads"

DEFAULT_APPLIED_STATE = {
    "latest_applied_release": None,
    "applied_at": None,
}


@dataclass(frozen=True)
class SyncResult:
    applied: bool
    tag: str | None
    rows: int
    reason: str


def load_json_file(path: Path, default: dict | None = None) -> dict:
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json_file(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_applied_state(path: Path = APPLIED_STATE_PATH) -> dict:
    state = load_json_file(path, default=DEFAULT_APPLIED_STATE.copy())
    return {
        "latest_applied_release": state.get("latest_applied_release"),
        "applied_at": state.get("applied_at"),
        "asset_name": state.get("asset_name"),
        "sha256": state.get("sha256"),
    }


def write_applied_state(path: Path, payload: dict) -> None:
    save_json_file(path, payload)


def get_manifest_target_tag(manifest: dict) -> str | None:
    latest_current = manifest.get("latest_current")
    if isinstance(latest_current, str) and latest_current:
        return latest_current
    if isinstance(latest_current, dict):
        for key in ("tag", "release_tag"):
            if latest_current.get(key):
                return str(latest_current[key])

    latest_release = manifest.get("latest_release")
    if isinstance(latest_release, str) and latest_release:
        return latest_release
    if isinstance(latest_release, dict) and latest_release.get("tag"):
        return str(latest_release["tag"])

    latest_delta = manifest.get("latest_delta") or {}
    if isinstance(latest_delta, dict) and latest_delta.get("tag"):
        return str(latest_delta["tag"])

    latest_full = manifest.get("latest_full")
    if isinstance(latest_full, dict) and latest_full.get("tag"):
        return str(latest_full["tag"])
    if isinstance(latest_full, str) and latest_full:
        return latest_full
    return None


def should_apply_release(target_tag: str | None, applied_state: dict) -> bool:
    if not target_tag:
        return False
    return target_tag != applied_state.get("latest_applied_release")


def resolve_target_release(client: GitHubReleaseClient, manifest: dict) -> ReleaseInfo:
    target_tag = get_manifest_target_tag(manifest)
    if target_tag:
        return client.get_release_by_tag(target_tag)
    return client.get_latest_release()


def choose_release_assets(release: ReleaseInfo) -> tuple[ReleaseAsset, ReleaseAsset | None]:
    parquet_asset = next((asset for asset in release.assets if asset.name.endswith(".parquet")), None)
    if parquet_asset is None:
        raise ValueError(f"release '{release.tag}' has no parquet asset")

    sha_asset = next(
        (
            asset
            for asset in release.assets
            if asset.name == f"{parquet_asset.name}.sha256"
            or asset.name == parquet_asset.name.replace(".parquet", ".sha256")
        ),
        None,
    )
    if sha_asset is None:
        sha_asset = next((asset for asset in release.assets if asset.name.endswith(".sha256")), None)
    return parquet_asset, sha_asset


def download_release_assets(
    client: GitHubReleaseClient,
    release: ReleaseInfo,
    download_dir: Path,
) -> tuple[Path, Path | None, ReleaseAsset]:
    parquet_asset, sha_asset = choose_release_assets(release)
    release_dir = download_dir / release.tag
    parquet_path = client.download_asset(parquet_asset, release_dir / parquet_asset.name)
    sha_path = None
    if sha_asset is not None:
        sha_path = client.download_asset(sha_asset, release_dir / sha_asset.name)
    return parquet_path, sha_path, parquet_asset


def read_expected_sha256(sha_path: Path | None, parquet_asset: ReleaseAsset) -> str:
    if sha_path is not None and sha_path.exists():
        return sha_path.read_text(encoding="utf-8").strip().split()[0]
    if parquet_asset.sha256:
        return parquet_asset.sha256.strip()
    raise ValueError("expected sha256 not found in asset metadata or .sha256 file")


def verify_downloaded_sha256(file_path: Path, expected_sha256: str, hash_fn: Callable[[Path], str]) -> str:
    actual_sha256 = hash_fn(file_path)
    if actual_sha256 != expected_sha256:
        raise ValueError(
            f"sha256 mismatch for {file_path.name}: expected={expected_sha256} actual={actual_sha256}"
        )
    return actual_sha256


def apply_release_parquet(
    parquet_path: Path,
    parquet_reader: Callable[[Path], pd.DataFrame],
    parquet_upserter: Callable[[pd.DataFrame], object],
) -> int:
    df = parquet_reader(parquet_path)
    parquet_upserter(df)
    return int(len(df))


def sync_latest_release(
    client: GitHubReleaseClient,
    *,
    manifest_path: Path = MANIFEST_PATH,
    applied_state_path: Path = APPLIED_STATE_PATH,
    download_dir: Path = DOWNLOAD_DIR,
    manifest_loader: Callable[[Path, dict | None], dict] = load_json_file,
    state_loader: Callable[[Path], dict] = read_applied_state,
    state_writer: Callable[[Path, dict], None] = write_applied_state,
    parquet_reader: Callable[[Path], pd.DataFrame] = pd.read_parquet,
    parquet_upserter: Callable[[pd.DataFrame], object] = parquet_store.upsert_ohlcv_from_df,
    hash_fn: Callable[[Path], str] = sha256_file,
) -> SyncResult:
    manifest = manifest_loader(manifest_path, {})
    applied_state = state_loader(applied_state_path)
    release = resolve_target_release(client, manifest)

    if not should_apply_release(release.tag, applied_state):
        return SyncResult(applied=False, tag=release.tag, rows=0, reason="already_applied")

    parquet_path, sha_path, parquet_asset = download_release_assets(client, release, download_dir)
    expected_sha256 = read_expected_sha256(sha_path, parquet_asset)
    actual_sha256 = verify_downloaded_sha256(parquet_path, expected_sha256, hash_fn)
    row_count = apply_release_parquet(parquet_path, parquet_reader, parquet_upserter)

    state_writer(
        applied_state_path,
        {
            "latest_applied_release": release.tag,
            "applied_at": datetime.now().isoformat(timespec="seconds"),
            "asset_name": parquet_asset.name,
            "sha256": actual_sha256,
        },
    )
    return SyncResult(applied=True, tag=release.tag, rows=row_count, reason="applied")


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply latest GitHub release parquet to local silver")
    parser.add_argument("--manifest-path", default=str(MANIFEST_PATH))
    parser.add_argument("--state-path", default=str(APPLIED_STATE_PATH))
    parser.add_argument("--download-dir", default=str(DOWNLOAD_DIR))
    args = parser.parse_args()

    client = GitHubReleaseClient()
    result = sync_latest_release(
        client,
        manifest_path=Path(args.manifest_path),
        applied_state_path=Path(args.state_path),
        download_dir=Path(args.download_dir),
    )
    print(
        json.dumps(
            {
                "applied": result.applied,
                "tag": result.tag,
                "rows": result.rows,
                "reason": result.reason,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
