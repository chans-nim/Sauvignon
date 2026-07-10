"""
`theme-sector-*`(및 구 `thema-sector-*`) GitHub Release에 첨부된 통합 `theme_history_snapshot.json`
또는 구 `theme_snapshot_*.json` 자산을 내려받아
`data/theme_history/` 를 채운다. CI에서는 수집 전에 실행해 영속 히스토리를 이어 받는다.

태그는 시간순(오래된 것 먼저)으로 처리해, 같은 영업일에 여러 번 릴리즈된 경우
나중 태그의 스냅샷이 덮어쓴다.

예:
  python -m scripts.sync_theme_history_from_github_releases --repo owner/Sauvignon
  python -m scripts.sync_theme_history_from_github_releases --repo owner/Sauvignon --max-releases 45
  python -m scripts.sync_theme_history_from_github_releases --tag-prefix theme-sector-
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

if __name__ == "__main__" and str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.theme_sector_release_lib import (  # noqa: E402
    THEME_HISTORY_SNAPSHOT_ASSET,
    is_theme_sector_release_tag,
    theme_sector_tag_sort_key,
)

THEME_SNAPSHOT_GLOB = "theme_snapshot_*.json"
OVERVIEW_ASSET = "overview.json"
THEME_HISTORY_FALLBACK_LEADER_COUNT = 3


def parse_repo_from_url(repo_or_url: str) -> str:
    s = repo_or_url.strip()
    if "github.com" in s:
        parts = s.rstrip("/").replace("https://", "").replace("http://", "").split("/")
        if "github.com" in parts:
            i = parts.index("github.com")
            if i + 2 <= len(parts):
                return f"{parts[i + 1]}/{parts[i + 2]}"
    return s


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


def pick_theme_snapshot_asset(assets: list) -> dict | None:
    combined = [
        a
        for a in assets
        if isinstance(a, dict) and str(a.get("name") or "") == THEME_HISTORY_SNAPSHOT_ASSET
    ]
    if combined:
        if len(combined) > 1:
            raise SystemExit(f"Expected one {THEME_HISTORY_SNAPSHOT_ASSET} asset")
        return combined[0]
    matches = [
        a
        for a in assets
        if isinstance(a, dict) and fnmatch.fnmatch(str(a.get("name") or ""), THEME_SNAPSHOT_GLOB)
    ]
    if not matches:
        return None
    if len(matches) > 1:
        names = ", ".join(sorted(str(a.get("name")) for a in matches))
        raise SystemExit(f"Expected one {THEME_SNAPSHOT_GLOB} asset, got: {names}")
    return matches[0]


def pick_asset_by_name(assets: list, name: str) -> dict | None:
    matches = [
        a
        for a in assets
        if isinstance(a, dict) and str(a.get("name") or "") == str(name)
    ]
    if len(matches) > 1:
        raise SystemExit(f"Expected one {name} asset")
    return matches[0] if matches else None


def _date_yyyymmdd(date_yyyy_mm_dd: str) -> str:
    return str(date_yyyy_mm_dd).strip().replace("-", "")[:8]


def _norm_stock_symbol(raw: object) -> str:
    s = str(raw or "").strip()
    return s.zfill(6) if s.isdigit() else s


def _parse_iso_date(s: object) -> str | None:
    st = str(s or "").strip()
    if len(st) >= 10 and st[4] == "-" and st[7] == "-":
        return st[:10]
    return None


def _history_display_path_from_row(r: dict) -> str:
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


def _rows_by_date(rows: list) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        d = str(row.get("date") or "").strip()
        if not d:
            continue
        out.setdefault(d, []).append(row)
    return out


def expand_combined_snapshot_to_daily_files(combined_path: Path, dest_dir: Path, *, overwrite: bool = True) -> list[Path]:
    """
    A release's theme_history_snapshot.json contains many dates. Store it as
    theme_snapshot_YYYYMMDD.json files so multiple release downloads do not
    overwrite each other under the same combined asset name.
    """
    payload = json.loads(Path(combined_path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return []
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    rows_by_date = _rows_by_date(list(payload.get("rows") or []))
    metric_by_date = _rows_by_date(list(payload.get("metric_rows") or []))
    dates = sorted(set(rows_by_date) | set(metric_by_date))
    written: list[Path] = []
    for d in dates:
        out_payload = {
            "schema_version": payload.get("schema_version", 2),
            "date": d,
            "rows": rows_by_date.get(d, []),
        }
        if "metric_rows" in payload:
            out_payload["metric_rows"] = metric_by_date.get(d, [])
        out_path = Path(dest_dir) / f"theme_snapshot_{_date_yyyymmdd(d)}.json"
        if out_path.exists() and not overwrite:
            continue
        out_path.write_text(json.dumps(out_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(out_path)
    return written


def _history_row_from_overview_row(row: dict, date_yyyy_mm_dd: str) -> dict:
    a = dict(row.get("analysis") or {})
    top_stocks: list[dict] = []
    for stock in list(row.get("major_stocks") or [])[:1]:
        if not isinstance(stock, dict):
            continue
        sym = _norm_stock_symbol(stock.get("symbol"))
        if not sym:
            continue
        top_stocks.append({"symbol": sym, "name": str(stock.get("name") or "").strip(), "rs": stock.get("rs")})
    return {
        "date": date_yyyy_mm_dd,
        "group_type": str(row.get("group_type") or ""),
        "major_category": row.get("major_category"),
        "middle_category": row.get("middle_category"),
        "display_path": _history_display_path_from_row(row),
        "theme_rs": float(a.get("relative_strength_score") or 0.0),
        "theme_rank": int(a.get("relative_strength_rank") or 0),
        "member_count": int(a.get("member_count") or 0),
        "top_members_value_sum": float(a.get("top_members_value_sum") or 0.0),
        "total_value_traded": float(a.get("total_value_traded") or 0.0),
        "up_ratio": float(a.get("up_ratio") or 0.0),
        "rs60_ratio": float(a.get("rs60_ratio") or 0.0),
        "rs70_ratio": float(a.get("rs70_ratio") or 0.0),
        "leader_status": "주도",
        "source_leader_status": str(a.get("leader_status") or ""),
        "leader_top_stocks": top_stocks,
    }


def _metric_row_from_overview_row(row: dict, date_yyyy_mm_dd: str) -> dict:
    a = dict(row.get("analysis") or {})
    return {
        "date": date_yyyy_mm_dd,
        "group_type": str(row.get("group_type") or ""),
        "major_category": row.get("major_category"),
        "middle_category": row.get("middle_category"),
        "display_path": _history_display_path_from_row(row),
        "theme_rs": float(a.get("relative_strength_score") or 0.0),
        "theme_rank": int(a.get("relative_strength_rank") or 0),
        "member_count": int(a.get("member_count") or 0),
        "top_members_value_sum": float(a.get("top_members_value_sum") or 0.0),
        "total_value_traded": float(a.get("total_value_traded") or 0.0),
        "up_ratio": float(a.get("up_ratio") or 0.0),
        "rs60_ratio": float(a.get("rs60_ratio") or 0.0),
        "rs70_ratio": float(a.get("rs70_ratio") or 0.0),
        "leader_status": str(a.get("leader_status") or ""),
    }


def write_daily_snapshot_from_overview(overview_path: Path, dest_dir: Path) -> Path | None:
    payload = json.loads(Path(overview_path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None
    meta = dict(payload.get("metadata") or {})
    snap_date = str(meta.get("theme_snapshot_date") or "").strip() or _parse_iso_date(meta.get("collected_at")) or ""
    if not snap_date:
        return None
    rows = [r for r in list(payload.get("major_rows") or []) + list(payload.get("middle_rows") or []) if isinstance(r, dict)]
    leaders = [r for r in rows if dict(r.get("analysis") or {}).get("leader_status") == "주도"]
    selected = leaders
    if not selected:
        selected = sorted(
            rows,
            key=lambda r: (
                -float(dict(r.get("analysis") or {}).get("relative_strength_score") or 0.0),
                int(dict(r.get("analysis") or {}).get("relative_strength_rank") or 999999),
                str(r.get("display_path") or ""),
            ),
        )[:THEME_HISTORY_FALLBACK_LEADER_COUNT]
    out_payload = {
        "schema_version": 2,
        "date": snap_date,
        "rows": [_history_row_from_overview_row(r, snap_date) for r in selected],
        "metric_rows": [_metric_row_from_overview_row(r, snap_date) for r in rows],
    }
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_path = dest_dir / f"theme_snapshot_{_date_yyyymmdd(snap_date)}.json"
    out_path.write_text(json.dumps(out_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def _normalize_explicit_prefix(raw: str | None) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    return s if s.endswith("-") else f"{s}-"


def http_list_theme_release_tags(repo: str, *, explicit_prefix: str | None) -> list[str]:
    tags: list[str] = []
    url: str | None = f"https://api.github.com/repos/{repo}/releases?per_page=100"
    while url:
        r = requests.get(url, headers=github_headers(binary=False), timeout=60)
        r.raise_for_status()
        for rel in r.json():
            if not isinstance(rel, dict) or rel.get("draft"):
                continue
            t = str(rel.get("tag_name") or "").strip()
            if is_theme_sector_release_tag(t, explicit_prefix=explicit_prefix):
                tags.append(t)
        url = (r.links.get("next") or {}).get("url") or None
    return tags


def gh_list_theme_release_tags(repo: str, *, explicit_prefix: str | None) -> list[str] | None:
    try:
        out = subprocess.check_output(
            [
                "gh",
                "release",
                "list",
                "--repo",
                repo,
                "--limit",
                "500",
                "--exclude-drafts",
                "--json",
                "tagName",
            ],
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, OSError):
        return None
    try:
        rows = json.loads(out)
    except json.JSONDecodeError:
        return None
    return [
        str(x.get("tagName") or "").strip()
        for x in rows
        if is_theme_sector_release_tag(str(x.get("tagName") or "").strip(), explicit_prefix=explicit_prefix)
    ]


def list_theme_release_tags(repo: str, *, explicit_prefix: str | None) -> list[str]:
    tags = gh_list_theme_release_tags(repo, explicit_prefix=explicit_prefix)
    if tags is None:
        tags = http_list_theme_release_tags(repo, explicit_prefix=explicit_prefix)
    uniq = sorted(set(tags))
    uniq.sort(key=theme_sector_tag_sort_key)
    return uniq


def fetch_release_assets(repo: str, tag: str) -> list:
    rel_url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    r = requests.get(rel_url, headers=github_headers(binary=False), timeout=30)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    payload = r.json()
    return list(payload.get("assets") or [])


def sync_theme_snapshots(
    repo: str,
    dest_dir: Path,
    *,
    explicit_prefix: str | None,
    max_releases: int,
) -> int:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    tags = list_theme_release_tags(repo, explicit_prefix=explicit_prefix)
    if max_releases > 0:
        tags = tags[-max_releases:]
    if not tags:
        hint = explicit_prefix or "theme-sector-* / legacy thema-sector-*"
        print(f"[sync-theme-history] no matching releases ({hint}) in {repo}")
        return 0
    print(f"[sync-theme-history] repo={repo} releases={len(tags)} dest={dest_dir}")
    n = 0
    for tag in tags:
        assets = fetch_release_assets(repo, tag)
        snap = pick_theme_snapshot_asset(assets)
        if not snap:
            print(f"[sync-theme-history] skip {tag}: no {THEME_HISTORY_SNAPSHOT_ASSET} / {THEME_SNAPSHOT_GLOB}")
            continue
        name = str(snap.get("name") or "")
        if name == THEME_HISTORY_SNAPSHOT_ASSET:
            with tempfile.TemporaryDirectory(prefix="theme-history-asset-") as td:
                tmp_path = Path(td) / name
                download_asset_by_api(repo, snap, tmp_path)
                written = expand_combined_snapshot_to_daily_files(tmp_path, dest_dir, overwrite=False)
            print(
                f"[sync-theme-history] {tag} -> {THEME_HISTORY_SNAPSHOT_ASSET} "
                f"expanded to {len(written)} daily snapshot(s)"
            )
        else:
            dest = dest_dir / name
            download_asset_by_api(repo, snap, dest)
            print(f"[sync-theme-history] {tag} -> {dest.name}")
        overview = pick_asset_by_name(assets, OVERVIEW_ASSET)
        if overview:
            with tempfile.TemporaryDirectory(prefix="theme-overview-asset-") as td:
                overview_path = Path(td) / OVERVIEW_ASSET
                download_asset_by_api(repo, overview, overview_path)
                daily_path = write_daily_snapshot_from_overview(overview_path, dest_dir)
            if daily_path:
                print(f"[sync-theme-history] {tag} -> {OVERVIEW_ASSET} rebuilt {daily_path.name}")
        n += 1
    return n


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download theme_snapshot JSONs from theme-sector (or legacy thema-sector) GitHub releases"
    )
    parser.add_argument("--repo", default=os.getenv("GITHUB_REPOSITORY", "chans-nim/Sauvignon"), help="owner/repo")
    parser.add_argument(
        "--dest-dir",
        type=Path,
        default=Path("data/theme_history"),
        help="Directory for theme_snapshot_*.json",
    )
    parser.add_argument(
        "--tag-prefix",
        default="",
        help="비우면 theme-sector-* 와 구 thema-sector-* 모두. 지정 시 해당 접두사만 (예: theme-sector-).",
    )
    parser.add_argument(
        "--max-releases",
        type=int,
        default=60,
        help="Maximum number of matching releases to apply (oldest dropped when over limit; 0 = no limit)",
    )
    args = parser.parse_args()
    repo = parse_repo_from_url(args.repo)
    explicit = _normalize_explicit_prefix(args.tag_prefix)
    n = sync_theme_snapshots(repo, args.dest_dir, explicit_prefix=explicit, max_releases=max(0, int(args.max_releases)))
    print(f"[sync-theme-history] done, downloaded {n} snapshot(s)")


if __name__ == "__main__":
    main()
