"""
수집 산출물을 `thema-sector-YYYYMMDD-HHMM` 태그로 GitHub Release에 올린다.

첨부:
  - theme_snapshot_YYYYMMDD.json (히스토리 디렉터리에서 당일 스냅샷)
  - overview.json, overview.html
  - thema_history_index.json (릴리즈 메타·동기화용)

예:
  GH_TOKEN=... python -m scripts.publish_thema_sector_release --repo owner/Sauvignon --tag thema-sector-20260507-1605
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

if __name__ == "__main__" and str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.thema_sector_release_lib import THEMA_SECTOR_TAG_PREFIX  # noqa: E402


def parse_repo_from_url(repo_or_url: str) -> str:
    s = repo_or_url.strip()
    if "github.com" in s:
        parts = s.rstrip("/").replace("https://", "").replace("http://", "").split("/")
        if "github.com" in parts:
            i = parts.index("github.com")
            if i + 2 <= len(parts):
                return f"{parts[i + 1]}/{parts[i + 2]}"
    return s


def pick_snapshot_for_date(history_dir: Path, snapshot_date_yyyy_mm_dd: str) -> Path:
    ymd = str(snapshot_date_yyyy_mm_dd).replace("-", "")
    p = history_dir / f"theme_snapshot_{ymd}.json"
    if not p.is_file():
        raise SystemExit(f"theme history snapshot not found: {p}")
    return p


def write_index_json(
    path: Path,
    *,
    release_tag: str,
    snapshot_path: Path,
    overview_json: Path,
    overview_html: Path,
) -> None:
    snap_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot_date = str(snap_payload.get("date") or "").strip()
    payload = {
        "schema_version": 1,
        "release_tag": release_tag,
        "snapshot_date": snapshot_date,
        "theme_snapshot_asset": snapshot_path.name,
        "overview_assets": [overview_json.name, overview_html.name],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def gh_release_exists(repo: str, tag: str) -> bool:
    r = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repo],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return r.returncode == 0


def gh_release_create(repo: str, tag: str, title: str, notes: str, files: list[Path]) -> None:
    cmd = [
        "gh",
        "release",
        "create",
        tag,
        "--repo",
        repo,
        "--title",
        title,
        "--notes",
        notes,
    ]
    cmd += [str(p) for p in files]
    subprocess.run(cmd, check=True)


def gh_release_upload(repo: str, tag: str, files: list[Path]) -> None:
    cmd = ["gh", "release", "upload", tag, "--repo", repo, "--clobber"]
    cmd += [str(p) for p in files]
    subprocess.run(cmd, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create GitHub release for thema sector collect outputs")
    parser.add_argument("--repo", default=os.getenv("GITHUB_REPOSITORY", "").strip() or None, help="owner/repo")
    parser.add_argument("--tag", required=True, help=f"Release tag, e.g. {THEMA_SECTOR_TAG_PREFIX}20260507-1605")
    parser.add_argument("--theme-history-dir", type=Path, default=Path("data/theme_history"))
    parser.add_argument("--out-dir", type=Path, default=Path("data/lake/sector/thema_major_middle"))
    parser.add_argument(
        "--snapshot-date",
        default=None,
        help="YYYY-MM-DD for theme_snapshot (default: read from overview.json metadata)",
    )
    args = parser.parse_args()
    repo = parse_repo_from_url(args.repo or "")
    if not repo or "/" not in repo:
        raise SystemExit("--repo or GITHUB_REPOSITORY is required")

    out_dir = Path(args.out_dir)
    overview_json = out_dir / "overview.json"
    overview_html = out_dir / "overview.html"
    if not overview_json.is_file() or not overview_html.is_file():
        raise SystemExit(f"missing overview files under {out_dir}")

    meta = json.loads(overview_json.read_text(encoding="utf-8")).get("metadata") or {}
    snap_date = (args.snapshot_date or meta.get("theme_snapshot_date") or "").strip()
    if not snap_date:
        raise SystemExit("could not resolve snapshot date; pass --snapshot-date YYYY-MM-DD")

    history_dir = Path(args.theme_history_dir)
    snapshot_path = pick_snapshot_for_date(history_dir, snap_date)

    with tempfile.TemporaryDirectory() as tmp:
        index_path = Path(tmp) / "thema_history_index.json"
        write_index_json(
            index_path,
            release_tag=args.tag,
            snapshot_path=snapshot_path,
            overview_json=overview_json,
            overview_html=overview_html,
        )
        title = f"Thema sector run {args.tag}"
        notes = (
            f"Theme snapshot: `{snapshot_path.name}`\n\n"
            "Prior snapshots: `python -m scripts.sync_theme_history_from_github_releases`"
        )
        files = [snapshot_path, overview_json, overview_html, index_path]
        if gh_release_exists(repo, args.tag):
            print(f"[publish-thema] release exists, uploading assets: {args.tag}")
            gh_release_upload(repo, args.tag, files)
        else:
            print(f"[publish-thema] creating release: {args.tag}")
            gh_release_create(repo, args.tag, title, notes, files)
    print("[publish-thema] done")


if __name__ == "__main__":
    main()
