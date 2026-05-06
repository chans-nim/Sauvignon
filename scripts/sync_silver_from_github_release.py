"""
GitHub Release에 올라간 스냅샷 parquet을 다운로드해서 로컬 silver(ohlcv_daily)에 반영한다.
이후 incremental/gap_fill/build_snapshot을 돌리기 위한 "베이스 데이터" 동기화 용도.

``--tag`` 미지정 시 원격 "최신"은 ``data-(snapshot|full|delta)-*`` 태그만 본다.
같은 저장소의 ``thema-sector-*`` 등 다른 릴리즈가 더 최근이어도 무시한다.

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
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

if __name__ == "__main__" and str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common.settings import settings
from src.common.utils import sha256_file
from src.storage import parquet_store

MANIFEST_PATH = settings.project_root / "data_manifest.json"
APPLIED_STATE_PATH = settings.project_root / "meta" / "applied_release_state.json"
DOWNLOAD_DIR = settings.project_root / "data" / "downloads"


def is_companion_parquet_name(name: str) -> bool:
    """Exclude ticker-state / output1-latest; sync silver uses only the main OHLCV snapshot."""
    n = str(name).lower()
    return n.endswith(".ticker-state.parquet") or n.endswith(".output1-latest.parquet")


def pick_parquet_release_asset(assets: list, tag: str) -> dict | None:
    """API asset dict for the main snapshot parquet (not companion files)."""
    by_name = {str(a.get("name") or ""): a for a in assets if isinstance(a, dict)}
    main_name = f"{tag}.parquet"
    if main_name in by_name:
        return by_name[main_name]
    candidates = [
        a
        for a in assets
        if isinstance(a, dict)
        and str(a.get("name") or "").endswith(".parquet")
        and not is_companion_parquet_name(str(a.get("name") or ""))
    ]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        return None
    names = ", ".join(sorted(str(a.get("name") or "") for a in candidates))
    raise SystemExit(
        f"Ambiguous main parquet for {tag!r} (API assets): {names}; expected {main_name} on the release."
    )


def resolve_main_parquet_path(out_dir: Path, tag: str) -> Path:
    """After gh/curl download, pick the OHLCV snapshot file matching the release tag."""
    preferred = out_dir / f"{tag}.parquet"
    if preferred.is_file():
        return preferred
    parquets = [
        p
        for p in out_dir.glob("*.parquet")
        if p.is_file() and not is_companion_parquet_name(p.name)
    ]
    if len(parquets) == 1:
        return parquets[0]
    if not parquets:
        raise SystemExit(f"No main snapshot parquet in {out_dir} (expected {tag}.parquet)")
    names = ", ".join(sorted(p.name for p in parquets))
    raise SystemExit(f"Ambiguous parquet files in {out_dir}: {names}; expected {tag}.parquet")


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


DATASET_RELEASE_TAG_RE = re.compile(r"^data-(?:snapshot|full|delta)-(\d{8})-(\d{4})$")


def is_dataset_release_tag(tag: str) -> bool:
    """Silver/스냅샷 동기화 대상 태그만 true (thema-sector-* 등 다른 릴리즈는 제외)."""
    return bool(DATASET_RELEASE_TAG_RE.match(str(tag).strip()))


def release_tag_sort_key(tag: str) -> tuple:
    m = DATASET_RELEASE_TAG_RE.match(str(tag))
    if m:
        return (0, m.group(1), m.group(2))
    return (1, str(tag), "")


def http_latest_dataset_tag(repo: str) -> str | None:
    """API 릴리즈 목록에서 가장 최근의 data-snapshot|full|delta 태그."""
    url: str | None = f"https://api.github.com/repos/{repo}/releases?per_page=100"
    while url:
        r = requests.get(url, headers=github_headers(binary=False), timeout=30)
        r.raise_for_status()
        batch = r.json()
        if not isinstance(batch, list):
            break
        for rel in batch:
            if not isinstance(rel, dict) or rel.get("draft") or rel.get("prerelease"):
                continue
            t = str(rel.get("tag_name") or "").strip()
            if is_dataset_release_tag(t):
                return t
        url = (r.links.get("next") or {}).get("url") or None
    return None


def _try_gh_list_latest_dataset_tag(repo: str) -> str | None:
    try:
        out = subprocess.check_output(
            [
                "gh",
                "release",
                "list",
                "--repo",
                repo,
                "--limit",
                "100",
                "--exclude-drafts",
                "--json",
                "tagName",
            ],
            text=True,
        ).strip()
        if not out:
            return None
        for row in json.loads(out):
            if not isinstance(row, dict):
                continue
            t = str(row.get("tagName") or "").strip()
            if is_dataset_release_tag(t):
                return t
    except (FileNotFoundError, subprocess.CalledProcessError, OSError, json.JSONDecodeError, TypeError):
        return None
    return None


def gh_latest_dataset_tag_optional(repo: str) -> str | None:
    return _try_gh_list_latest_dataset_tag(repo) or http_latest_dataset_tag(repo)


def gh_latest_dataset_tag(repo: str) -> str:
    """최신 data-snapshot|full|delta 릴리즈 태그 (thema-sector 등 비데이터셋 릴리즈는 건너뜀)."""
    out = gh_latest_dataset_tag_optional(repo)
    if not out:
        raise SystemExit(
            f"No data-(snapshot|full|delta)-* releases found in {repo} "
            "(non-dataset releases, e.g. thema-sector-*, are ignored for silver sync)."
        )
    return out


def github_token() -> str:
    return (
        os.getenv("GH_PAT_SAUVIGNON")
        or os.getenv("GITHUB_TOKEN")
        or os.getenv("GH_TOKEN")
        or os.getenv("GH_PAT")
        or ""
    ).strip()


def github_headers(*, binary: bool = False) -> dict[str, str]:
    headers: dict[str, str] = {
        "X-GitHub-Api-Version": "2022-11-28",
        "Accept": "application/octet-stream" if binary else "application/vnd.github+json",
    }
    token = github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def gh_download_release_assets(repo: str, tag: str, out_dir: Path) -> tuple[Path, Path | None]:
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
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
    except (FileNotFoundError, subprocess.CalledProcessError, OSError):
        return http_download_release_assets(repo, tag, out_dir)
    if not any(out_dir.glob("*.parquet")):
        raise SystemExit(f"No parquet asset downloaded for {repo}@{tag}")
    parquet_path = resolve_main_parquet_path(out_dir, tag)
    sha_named = out_dir / f"{tag}.sha256"
    sha_files = sorted(out_dir.glob("*.sha256"))
    if sha_named.is_file():
        sha_path = sha_named
    else:
        sha_path = sha_files[0] if sha_files else None
    return parquet_path, sha_path


def http_download_release_assets(repo: str, tag: str, out_dir: Path) -> tuple[Path, Path | None]:
    rel_url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    r = requests.get(rel_url, headers=github_headers(binary=False), timeout=30)
    r.raise_for_status()
    payload = r.json()
    assets = payload.get("assets") or []
    parquet_asset = pick_parquet_release_asset(assets, tag)
    sha_main = f"{tag}.sha256"
    by_asset_name = {str(a.get("name") or ""): a for a in assets if isinstance(a, dict)}
    sha_asset = by_asset_name.get(sha_main) or next(
        (a for a in assets if isinstance(a, dict) and str(a.get("name") or "").endswith(".sha256")),
        None,
    )
    if not parquet_asset:
        raise SystemExit(f"No main parquet assets found in {repo}@{tag} (need {tag}.parquet or a single non-companion .parquet)")
    parquet_path = download_asset_by_api(repo, parquet_asset, out_dir / str(parquet_asset.get("name") or f"{tag}.parquet"))
    sha_path = None
    if sha_asset:
        sha_path = download_asset_by_api(repo, sha_asset, out_dir / str(sha_asset.get("name") or f"{tag}.sha256"))
    return parquet_path, sha_path


def download_asset_by_api(repo: str, asset: dict, dest: Path) -> Path:
    asset_id = asset.get("id")
    if not asset_id:
        raise SystemExit(f"Missing asset id for {asset.get('name')}")
    url = f"https://api.github.com/repos/{repo}/releases/assets/{asset_id}"
    with requests.get(url, headers=github_headers(binary=True), stream=True, timeout=300, allow_redirects=True) as r:
        r.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                if chunk:
                    f.write(chunk)
    return dest


def resolve_default_tag(repo: str) -> str:
    """
    --tag 미지정 시, 로컬 manifest와 원격 최신 릴리즈 중 더 최신 태그를 선택한다.
    """
    manifest_tag = load_manifest_tag()
    remote_tag = gh_latest_dataset_tag(repo)
    if manifest_tag and remote_tag:
        if release_tag_sort_key(remote_tag) > release_tag_sort_key(manifest_tag):
            print(f"[sync] note: manifest tag={manifest_tag} is older than remote latest={remote_tag}; using remote latest")
            return remote_tag
        return manifest_tag
    return manifest_tag or remote_tag


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
    tag = args.tag or resolve_default_tag(repo)
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

