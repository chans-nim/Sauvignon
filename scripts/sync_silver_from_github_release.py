"""
GitHub Release에 올라간 스냅샷 parquet을 다운로드해서 로컬 silver(ohlcv_daily)에 반영한다.
이후 incremental/gap_fill/build_snapshot을 돌리기 위한 "베이스 데이터" 동기화 용도.

요구사항:
- gh CLI 필요 (GitHub Actions runner에는 기본 제공)
- 권한: Release asset 다운로드 가능해야 함 (actions에서는 GH_TOKEN 사용)

사용 예:
  python -m scripts.sync_silver_from_github_release --repo chans-nim/Sauvignon
  python -m scripts.sync_silver_from_github_release --repo chans-nim/Sauvignon --tag data-snapshot-20260317-1338
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

if __name__ == "__main__" and str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common.settings import settings
from src.common.utils import sha256_file
from src.storage import parquet_store

MANIFEST_PATH = settings.project_root / "data_manifest.json"
APPLIED_STATE_PATH = settings.project_root / "meta" / "applied_release_state.json"
DOWNLOAD_DIR = settings.project_root / "data" / "downloads"


def parse_repo_from_url(repo_or_url: str) -> str:
    s = repo_or_url.strip()
    if "github.com" in s:
        parts = s.rstrip("/").replace("https://", "").replace("http://", "").split("/")
        if "github.com" in parts:
            i = parts.index("github.com")
            if i + 2 <= len(parts):
                return f"{parts[i + 1]}/{parts[i + 2]}"
    return s


def load_manifest_tag(path: Path = MANIFEST_PATH) -> str | None:
    if not path.exists():
        return None
    m = json.loads(path.read_text(encoding="utf-8"))
    latest = m.get("latest_current")
    if isinstance(latest, dict) and latest.get("tag"):
        return str(latest["tag"])
    if isinstance(latest, str) and latest:
        return latest
    return None


def gh_latest_tag(repo: str) -> str:
    # 최신 1개 태그만
    out = subprocess.check_output(
        ["gh", "release", "list", "--repo", repo, "--limit", "1", "--json", "tagName", "-q", ".[0].tagName"],
        text=True,
    ).strip()
    if not out:
        raise SystemExit(f"No releases found in {repo}")
    return out


def gh_download_release_assets(repo: str, tag: str, out_dir: Path) -> tuple[Path, Path | None]:
    out_dir.mkdir(parents=True, exist_ok=True)
    # parquet
    subprocess.run(
        ["gh", "release", "download", tag, "--repo", repo, "-D", str(out_dir), "-p", "*.parquet"],
        check=True,
    )
    # sha256 (optional)
    subprocess.run(
        ["gh", "release", "download", tag, "--repo", repo, "-D", str(out_dir), "-p", "*.sha256"],
        check=False,
    )
    parquets = sorted(out_dir.glob("*.parquet"))
    if not parquets:
        raise SystemExit(f"No parquet asset downloaded for {repo}@{tag}")
    parquet_path = parquets[0]
    sha_files = sorted(out_dir.glob("*.sha256"))
    sha_path = sha_files[0] if sha_files else None
    return parquet_path, sha_path


def read_expected_sha256(sha_path: Path | None) -> str | None:
    if not sha_path or not sha_path.exists():
        return None
    return sha_path.read_text(encoding="utf-8").split()[0].strip()


def save_applied_state(tag: str, asset_name: str, sha256: str, rows: int) -> None:
    payload = {
        "latest_applied_release": tag,
        "applied_at": datetime.now().isoformat(timespec="seconds"),
        "asset_name": asset_name,
        "sha256": sha256,
        "rows": int(rows),
    }
    APPLIED_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    APPLIED_STATE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download GitHub Release snapshot and upsert into silver")
    parser.add_argument("--repo", default="chans-nim/Sauvignon", help="owner/repo or GitHub URL")
    parser.add_argument("--tag", default=None, help="Release tag (default: data_manifest.latest_current or gh latest)")
    parser.add_argument("--download-dir", type=Path, default=DOWNLOAD_DIR, help="Download directory")
    parser.add_argument("--skip-sha256", action="store_true", help="Skip sha256 verification even if .sha256 exists")
    args = parser.parse_args()

    repo = parse_repo_from_url(args.repo)
    tag = args.tag or load_manifest_tag() or gh_latest_tag(repo)
    release_dir = args.download_dir / tag

    print(f"[sync] repo={repo} tag={tag}")
    parquet_path, sha_path = gh_download_release_assets(repo, tag, release_dir)
    print(f"[downloaded] parquet={parquet_path}")
    if sha_path:
        print(f"[downloaded] sha256={sha_path}")

    expected = read_expected_sha256(sha_path)
    actual = sha256_file(parquet_path)
    if expected and not args.skip_sha256:
        if actual != expected:
            raise SystemExit(f"sha256 mismatch: expected={expected} actual={actual}")
        print("[sha256] OK")
    else:
        print("[sha256] expected missing or skipped")

    print("[apply] reading parquet...")
    df = pd.read_parquet(parquet_path)
    print(f"[apply] rows={len(df):,}")
    parquet_store.upsert_ohlcv_from_df(df)
    save_applied_state(tag=tag, asset_name=parquet_path.name, sha256=actual, rows=len(df))
    print("[apply] OK")


if __name__ == "__main__":
    main()

