"""
수집 산출물을 `theme-sector-YYYYMMDD-HHMM` 태그로 GitHub Release에 올린다.

첨부:
  - theme_history_snapshot.json (히스토리 디렉터리의 기존/당일 스냅샷을 주도 테마만 통합)
  - overview.json, overview.html
  - theme_history_index.json (릴리즈 메타·동기화용)
  - theme_major_middle_stock_classification_dup_allowed.json (있을 때)

예:
  GH_TOKEN=... python -m scripts.publish_theme_sector_release --repo owner/Sauvignon --tag theme-sector-20260507-1605
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

from scripts.theme_sector_release_lib import THEME_HISTORY_SNAPSHOT_ASSET, THEME_SECTOR_TAG_PREFIX  # noqa: E402


def parse_repo_from_url(repo_or_url: str) -> str:
    s = repo_or_url.strip()
    if "github.com" in s:
        parts = s.rstrip("/").replace("https://", "").replace("http://", "").split("/")
        if "github.com" in parts:
            i = parts.index("github.com")
            if i + 2 <= len(parts):
                return f"{parts[i + 1]}/{parts[i + 2]}"
    return s


def _date_yyyymmdd(date_yyyy_mm_dd: str) -> str:
    return str(date_yyyy_mm_dd).strip().replace("-", "")[:8]


def _display_path_from_row(r: dict) -> str:
    dp = str(r.get("display_path") or "").strip()
    if dp:
        return dp
    major = str(r.get("major_category") or "").strip()
    middle = r.get("middle_category")
    gt = str(r.get("group_type") or "")
    if gt == "major":
        return major or "-"
    mid = "" if middle is None else str(middle).strip()
    if major and mid:
        return f"{major} > {mid}"
    return major or mid or "-"


def _normalize_leader_row(r: dict) -> dict | None:
    if str(r.get("leader_status") or "").strip() != "주도":
        return None
    d = str(r.get("date") or "").strip()
    if not d:
        return None
    stocks: list[dict] = []
    for s in list(r.get("leader_top_stocks") or [])[:1]:
        if not isinstance(s, dict):
            continue
        sym = str(s.get("symbol") or "").strip()
        if not sym:
            continue
        stocks.append(
            {
                "symbol": sym.zfill(6) if sym.isdigit() else sym,
                "name": str(s.get("name") or "").strip(),
                "rs": s.get("rs"),
            }
        )
    return {
        "date": d,
        "group_type": str(r.get("group_type") or ""),
        "major_category": r.get("major_category"),
        "middle_category": r.get("middle_category"),
        "display_path": _display_path_from_row(r),
        "theme_rs": float(r.get("theme_rs") or 0.0),
        "theme_rank": int(r.get("theme_rank") or 0),
        "member_count": int(r.get("member_count") or 0),
        "top_members_value_sum": float(r.get("top_members_value_sum") or 0.0),
        "total_value_traded": float(r.get("total_value_traded") or 0.0),
        "up_ratio": float(r.get("up_ratio") or 0.0),
        "rs60_ratio": float(r.get("rs60_ratio") or 0.0),
        "rs70_ratio": float(r.get("rs70_ratio") or 0.0),
        "leader_status": "주도",
        "leader_top_stocks": stocks,
    }


def _normalize_metric_row(r: dict) -> dict | None:
    d = str(r.get("date") or "").strip()
    if not d:
        return None
    return {
        "date": d,
        "group_type": str(r.get("group_type") or ""),
        "major_category": r.get("major_category"),
        "middle_category": r.get("middle_category"),
        "display_path": _display_path_from_row(r),
        "theme_rs": float(r.get("theme_rs") or 0.0),
        "theme_rank": int(r.get("theme_rank") or 0),
        "member_count": int(r.get("member_count") or 0),
        "top_members_value_sum": float(r.get("top_members_value_sum") or 0.0),
        "total_value_traded": float(r.get("total_value_traded") or 0.0),
        "up_ratio": float(r.get("up_ratio") or 0.0),
        "rs60_ratio": float(r.get("rs60_ratio") or 0.0),
        "rs70_ratio": float(r.get("rs70_ratio") or 0.0),
        "leader_status": str(r.get("leader_status") or ""),
    }


def _read_snapshot_rows(path: Path, *, leaders_only: bool = False) -> list[dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(payload, dict):
        raw_rows = payload.get("rows") if leaders_only else payload.get("metric_rows") or payload.get("rows")
    else:
        raw_rows = payload
    if not isinstance(raw_rows, list):
        return []
    rows: list[dict] = []
    for r in raw_rows:
        if isinstance(r, dict):
            nr = _normalize_leader_row(r) if leaders_only else _normalize_metric_row(r)
            if nr is not None:
                rows.append(nr)
    return rows


def write_combined_snapshot(history_dir: Path, snapshot_date_yyyy_mm_dd: str, out_path: Path) -> Path:
    files: list[Path] = []
    combined = Path(history_dir) / THEME_HISTORY_SNAPSHOT_ASSET
    if combined.is_file():
        files.append(combined)
    files.extend(sorted(Path(history_dir).glob("theme_snapshot_*.json"), key=lambda p: p.name))
    today_file = Path(history_dir) / f"theme_snapshot_{_date_yyyymmdd(snapshot_date_yyyy_mm_dd)}.json"
    if not today_file.is_file() and not combined.is_file():
        raise SystemExit(f"theme history snapshot not found: {today_file}")

    by_key: dict[tuple[str, str, str, str, str], dict] = {}
    metric_by_key: dict[tuple[str, str, str, str, str], dict] = {}
    for p in files:
        for r in _read_snapshot_rows(p, leaders_only=True):
            key = (
                str(r.get("date") or ""),
                str(r.get("group_type") or ""),
                str(r.get("major_category") or ""),
                str(r.get("middle_category") or ""),
                str(r.get("display_path") or ""),
            )
            by_key[key] = r
        for r in _read_snapshot_rows(p, leaders_only=False):
            key = (
                str(r.get("date") or ""),
                str(r.get("group_type") or ""),
                str(r.get("major_category") or ""),
                str(r.get("middle_category") or ""),
                str(r.get("display_path") or ""),
            )
            metric_by_key[key] = r
    rows = list(by_key.values())
    metric_rows = list(metric_by_key.values())
    rows.sort(
        key=lambda r: (
            str(r.get("date") or ""),
            str(r.get("group_type") or ""),
            str(r.get("display_path") or ""),
        ),
        reverse=True,
    )
    metric_rows.sort(
        key=lambda r: (
            str(r.get("date") or ""),
            str(r.get("group_type") or ""),
            str(r.get("display_path") or ""),
        ),
        reverse=True,
    )
    payload = {
        "schema_version": 2,
        "date": str(snapshot_date_yyyy_mm_dd).strip(),
        "rows": rows,
        "metric_rows": metric_rows,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def write_index_json(
    path: Path,
    *,
    release_tag: str,
    snapshot_path: Path,
    overview_json: Path,
    overview_html: Path,
    classification_json: Path | None = None,
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
    if classification_json and classification_json.is_file():
        payload["classification_asset"] = classification_json.name
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
    parser = argparse.ArgumentParser(description="Create GitHub release for theme sector collect outputs")
    parser.add_argument("--repo", default=os.getenv("GITHUB_REPOSITORY", "").strip() or None, help="owner/repo")
    parser.add_argument("--tag", required=True, help=f"Release tag, e.g. {THEME_SECTOR_TAG_PREFIX}20260507-1605")
    parser.add_argument("--theme-history-dir", type=Path, default=Path("data/theme_history"))
    parser.add_argument("--out-dir", type=Path, default=Path("data/lake/sector/thema_major_middle"))
    parser.add_argument(
        "--classification-json",
        type=Path,
        default=Path("data/theme/theme_major_middle_stock_classification_dup_allowed.json"),
        help="Theme classification JSON to attach to the release (optional; default: data/theme/...).",
    )
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
    classification_json = Path(args.classification_json)
    if not classification_json.is_file():
        classification_json = None

    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = write_combined_snapshot(
            history_dir,
            snap_date,
            Path(tmp) / THEME_HISTORY_SNAPSHOT_ASSET,
        )
        index_path = Path(tmp) / "theme_history_index.json"
        write_index_json(
            index_path,
            release_tag=args.tag,
            snapshot_path=snapshot_path,
            overview_json=overview_json,
            overview_html=overview_html,
            classification_json=classification_json,
        )
        title = f"Theme sector run {args.tag}"
        notes = (
            f"Theme history snapshot: `{snapshot_path.name}`\n\n"
            "Prior snapshots: `python -m scripts.sync_theme_history_from_github_releases`"
        )
        files = [snapshot_path, overview_json, overview_html, index_path]
        if classification_json:
            files.append(classification_json)
        if gh_release_exists(repo, args.tag):
            print(f"[publish-theme] release exists, uploading assets: {args.tag}")
            gh_release_upload(repo, args.tag, files)
        else:
            print(f"[publish-theme] creating release: {args.tag}")
            gh_release_create(repo, args.tag, title, notes, files)
    print("[publish-theme] done")


if __name__ == "__main__":
    main()
