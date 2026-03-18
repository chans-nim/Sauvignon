"""
GitHub Release에서 스냅샷 parquet를 다운로드하고 유효성(요약·무결성)을 확인합니다.
meta.duckdb가 있으면 validate_snapshot을 추가로 실행해 universe 대비 검증까지 수행합니다.

사용 (프로젝트 루트에서):
  python -m scripts.download_and_validate_release
  python -m scripts.download_and_validate_release --tag data-snapshot-20260317-1338
  python -m scripts.download_and_validate_release --tag data-snapshot-20260317-1338 --repo chans-nim/Sauvignon
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

import requests

if __name__ == "__main__" and str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common.settings import settings

DEFAULT_REPO = "chans-nim/Sauvignon"
DEFAULT_TAG = "data-snapshot-20260317-1338"
DOWNLOAD_DIR = settings.project_root / "data" / "downloads"
META_DB = settings.project_root / "meta" / "meta.duckdb"


def get_release_by_tag(repo: str, tag: str) -> dict:
    url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json()


def download_asset(browser_download_url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(browser_download_url, stream=True, timeout=60)
    r.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in r.iter_content(chunk_size=1 << 20):
            if chunk:
                f.write(chunk)
    return dest


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_summary_and_integrity(parquet_path: Path) -> None:
    import duckdb
    p = parquet_path.as_posix()
    con = duckdb.connect()
    try:
        print("[summary]")
        print(
            con.execute(
                """
                SELECT
                    COUNT(*) AS total_rows,
                    COUNT(DISTINCT symbol) AS symbols,
                    MIN(date) AS min_date,
                    MAX(date) AS max_date
                FROM read_parquet(?)
                """,
                [p],
            ).fetchdf().to_string(index=False)
        )
        print()
        print("[integrity]")
        print(
            con.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM (
                        SELECT symbol, date
                        FROM read_parquet(?)
                        GROUP BY symbol, date
                        HAVING COUNT(*) > 1
                    )) AS duplicate_symbol_date_keys,
                    (SELECT COUNT(*) FROM read_parquet(?) WHERE close <= 0 OR volume < 0) AS invalid_price_or_volume_rows
                """,
                [p, p],
            ).fetchdf().to_string(index=False)
        )
        print()
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download snapshot from GitHub Release and run validation"
    )
    parser.add_argument("--tag", default=DEFAULT_TAG, help=f"Release tag (default: {DEFAULT_TAG})")
    parser.add_argument("--repo", default=DEFAULT_REPO, help=f"Repo owner/name (default: {DEFAULT_REPO})")
    parser.add_argument("--download-dir", type=Path, default=DOWNLOAD_DIR, help="Directory to save parquet")
    parser.add_argument("--expected-sha256", default=None, help="If set, verify downloaded file sha256")
    parser.add_argument("--skip-full-validate", action="store_true", help="Skip validate_snapshot (universe check) even if meta exists")
    args = parser.parse_args()

    tag = args.tag.strip()
    release_dir = args.download_dir / tag

    print(f"Fetching release: {args.repo} @ {tag}")
    release = get_release_by_tag(args.repo, tag)
    assets = release.get("assets") or []
    parquet_asset = next(
        (a for a in assets if (a.get("name") or "").endswith(".parquet")),
        None,
    )
    if not parquet_asset:
        print(f"No parquet asset found in release. Assets: {[a.get('name') for a in assets]}")
        sys.exit(1)
    parquet_name = parquet_asset["name"]
    parquet_path = release_dir / parquet_name

    browser_url = parquet_asset.get("browser_download_url")
    if not browser_url:
        print("Asset has no browser_download_url")
        sys.exit(1)

    print(f"Downloading {parquet_name} ...")
    download_asset(browser_url, parquet_path)
    print(f"Saved: {parquet_path}")

    actual_sha256 = sha256_file(parquet_path)
    print(f"sha256: {actual_sha256}")
    if args.expected_sha256:
        expected = args.expected_sha256.strip().lower()
        if actual_sha256.lower() != expected:
            print(f"sha256 mismatch: expected {expected}")
            sys.exit(1)
        print("sha256 OK")
    print()

    validate_summary_and_integrity(parquet_path)

    if not args.skip_full_validate and META_DB.exists():
        print("Running full validation (universe vs snapshot, short years) ...")
        print()
        subprocess.run(
            [sys.executable, "-m", "scripts.validate_snapshot", "--file-path", str(parquet_path)],
            cwd=settings.project_root,
            check=False,
        )
    elif not META_DB.exists():
        print("(meta.duckdb not found; skipping universe/short-year checks.)")


if __name__ == "__main__":
    main()
