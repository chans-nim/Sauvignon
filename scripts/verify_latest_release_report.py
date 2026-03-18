# verify_latest_release_report.py
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

if __name__ == "__main__" and str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common.settings import settings

DEFAULT_REPO = "chans-nim/Sauvignon"
DEFAULT_RELEASES_URL = "https://github.com/chans-nim/Sauvignon/releases"
DOWNLOAD_DIR = settings.project_root / "data" / "downloads"

META_DB = settings.project_root / "meta" / "meta.duckdb"
TARGET_START = "2016-01-01"
TARGET_END = "2025-12-31"
MIN_ROWS_PER_YEAR = 200
MAX_DAYS_BEHIND_FOR_FRESH = 7


def parse_repo_from_url(repo_or_url: str) -> str:
    """
    Normalize to owner/repo.

    Accepts:
    - chans-nim/Sauvignon
    - https://github.com/chans-nim/Sauvignon
    - https://github.com/chans-nim/Sauvignon/releases
    - git@github.com:chans-nim/Sauvignon.git
    """
    s = repo_or_url.strip().rstrip("/")
    if not s:
        raise ValueError("repo is empty")

    if re.fullmatch(r"[^/]+/[^/]+", s):
        return s

    if s.startswith("git@github.com:"):
        s = s.replace("git@github.com:", "", 1)
        if s.endswith(".git"):
            s = s[:-4]
        return s

    if "github.com" in s:
        u = urlparse(s if "://" in s else f"https://{s}")
        parts = [p for p in u.path.split("/") if p]
        if len(parts) >= 2:
            owner, repo = parts[0], parts[1]
            if repo.endswith(".git"):
                repo = repo[:-4]
            return f"{owner}/{repo}"

    raise ValueError(f"Unable to parse owner/repo from: {repo_or_url}")


def github_token() -> str:
    return (
        os.getenv("GH_PAT_SAUVIGNON")
        or os.getenv("GITHUB_TOKEN")
        or os.getenv("GH_TOKEN")
        or os.getenv("GH_PAT")
        or ""
    ).strip()


def github_headers(*, binary: bool = False) -> dict[str, str]:
    """
    Build GitHub API headers.

    binary=False: JSON API calls
    binary=True : release asset download calls
    """
    token = github_token()
    headers: dict[str, str] = {
        "X-GitHub-Api-Version": "2022-11-28",
        "Accept": "application/octet-stream" if binary else "application/vnd.github+json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def ensure_private_repo_auth(repo: str) -> None:
    """
    Fail early with a useful message if token is missing.

    Public repos also work without token, but private repos won't.
    """
    if github_token():
        return
    print(
        f"[WARN] No GitHub token found. Public repos may work, but private repo access may fail: {repo}\n"
        "Set one of: GH_PAT_SAUVIGNON, GITHUB_TOKEN, GH_TOKEN, GH_PAT"
    )


def request_json(url: str) -> dict | list:
    r = requests.get(url, headers=github_headers(binary=False), timeout=30)
    if r.status_code == 404:
        # Keep raw 404 for caller logic
        r.raise_for_status()
    r.raise_for_status()
    return r.json()


def get_latest_release(repo: str) -> dict:
    ensure_private_repo_auth(repo)

    latest_url = f"https://api.github.com/repos/{repo}/releases/latest"
    r = requests.get(latest_url, headers=github_headers(binary=False), timeout=30)

    if r.status_code == 404:
        list_url = f"https://api.github.com/repos/{repo}/releases"
        r2 = requests.get(list_url, headers=github_headers(binary=False), timeout=30)

        if r2.status_code == 404:
            token_present = bool(github_token())
            raise SystemExit(
                f"GitHub API returned 404 for repo '{repo}'.\\n"
                f"- token present: {token_present}\\n"
                f"- if repo is private, verify token is set and has access to this repository\\n"
                f"- if using a fine-grained PAT, check repository selection and Contents: Read permission\\n"
                f"- also verify owner/repo name is correct"
            )

        r2.raise_for_status()
        releases = r2.json()
        if not releases:
            raise SystemExit(
                "No releases found. Repository is reachable, but there are no releases."
            )

        for rel in releases:
            if not rel.get("draft") and not rel.get("prerelease"):
                return rel
        return releases[0]

    r.raise_for_status()
    return r.json()

def get_release_by_tag(repo: str, tag: str) -> dict:
    ensure_private_repo_auth(repo)
    url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    r = requests.get(url, headers=github_headers(binary=False), timeout=30)
    r.raise_for_status()
    return r.json()


def select_assets(release: dict) -> dict[str, dict | None]:
    assets = release.get("assets") or []

    parquet_asset = next((a for a in assets if (a.get("name") or "").endswith(".parquet")), None)
    sha_asset = next((a for a in assets if (a.get("name") or "").endswith(".sha256")), None)
    json_asset = next((a for a in assets if (a.get("name") or "").endswith(".json")), None)

    return {
        "parquet": parquet_asset,
        "sha256": sha_asset,
        "json": json_asset,
    }


def download_asset_by_api(repo: str, asset: dict, dest: Path) -> Path:
    """
    Download a release asset through the GitHub API.

    This is safer for private repos than using browser_download_url directly.
    """
    asset_id = asset["id"]
    url = f"https://api.github.com/repos/{repo}/releases/assets/{asset_id}"

    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(
        url,
        headers=github_headers(binary=True),
        stream=True,
        timeout=300,
        allow_redirects=True,
    ) as r:
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


def read_expected_sha256(sha_path: Path) -> str:
    text = sha_path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"Empty sha256 file: {sha_path}")

    # support:
    # "<hash>"
    # "<hash>  filename"
    return text.split()[0].strip()


def verify_sha256_if_available(parquet_path: Path, sha_path: Path | None) -> tuple[str, str | None, bool | None]:
    actual = sha256_file(parquet_path)
    if not sha_path or not sha_path.exists():
        return actual, None, None

    expected = read_expected_sha256(sha_path)
    return actual, expected, (actual.lower() == expected.lower())


def run_validation(parquet_path: Path, meta_exists: bool) -> dict:
    import duckdb

    p = parquet_path.as_posix()
    con = duckdb.connect()
    report = {
        "tag": None,
        "total_rows": None,
        "symbols": None,
        "min_date": None,
        "max_date": None,
        "duplicate_keys": None,
        "invalid_rows": None,
        "integrity_ok": None,
        "max_date_str": None,
        "days_behind": None,
        "is_fresh": None,
        "symbols_at_max_date": None,
        "coverage_by_year": None,
        "universe_active": None,
        "snapshot_symbols": None,
        "missing_in_snapshot": None,
        "short_symbol_years_count": None,
        "short_symbol_years_by_year": None,
        "missing_2016": None,
        "missing_2025": None,
    }
    try:
        summary = con.execute(
            """
            SELECT
                COUNT(*) AS total_rows,
                COUNT(DISTINCT symbol) AS symbols,
                MIN(date)::VARCHAR AS min_date,
                MAX(date)::VARCHAR AS max_date
            FROM read_parquet(?)
            """,
            [p],
        ).fetchdf()
        row = summary.iloc[0]
        report["total_rows"] = int(row["total_rows"])
        report["symbols"] = int(row["symbols"])
        report["min_date"] = str(row["min_date"]) if row["min_date"] else None
        report["max_date_str"] = str(row["max_date"]) if row["max_date"] else None
        report["max_date"] = report["max_date_str"]

        dup_invalid = con.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM (
                    SELECT symbol, date FROM read_parquet(?)
                    GROUP BY symbol, date HAVING COUNT(*) > 1
                )) AS dup,
                (SELECT COUNT(*) FROM read_parquet(?) WHERE close <= 0 OR volume < 0) AS inv
            """,
            [p, p],
        ).fetchdf()
        report["duplicate_keys"] = int(dup_invalid.iloc[0]["dup"])
        report["invalid_rows"] = int(dup_invalid.iloc[0]["inv"])
        report["integrity_ok"] = report["duplicate_keys"] == 0 and report["invalid_rows"] == 0

        if report["max_date_str"]:
            max_d = datetime.strptime(report["max_date_str"][:10], "%Y-%m-%d").date()
            today = date.today()
            report["days_behind"] = (today - max_d).days
            report["is_fresh"] = report["days_behind"] <= MAX_DAYS_BEHIND_FOR_FRESH

        at_max = con.execute(
            """
            SELECT COUNT(DISTINCT symbol) AS cnt
            FROM read_parquet(?)
            WHERE date = (SELECT MAX(date) FROM read_parquet(?))
            """,
            [p, p],
        ).fetchdf()
        report["symbols_at_max_date"] = int(at_max.iloc[0]["cnt"])

        coverage = con.execute(
            """
            SELECT
                year(date) AS year,
                COUNT(DISTINCT symbol) AS symbols,
                COUNT(*) AS rows,
                MIN(date)::VARCHAR AS y_min,
                MAX(date)::VARCHAR AS y_max
            FROM read_parquet(?)
            WHERE date >= ? AND date <= ?
            GROUP BY year(date)
            ORDER BY year(date)
            """,
            [p, TARGET_START, TARGET_END],
        ).fetchdf()
        report["coverage_by_year"] = coverage

        if meta_exists:
            con.execute(f"ATTACH '{META_DB.as_posix()}' AS meta (READ_ONLY)")
            uv = con.execute(
                """
                WITH active AS (SELECT symbol FROM meta.universe WHERE is_active = TRUE),
                     snap AS (SELECT DISTINCT symbol FROM read_parquet(?))
                SELECT
                    (SELECT COUNT(*) FROM active) AS active,
                    (SELECT COUNT(*) FROM snap) AS snap,
                    (SELECT COUNT(*) FROM active a LEFT JOIN snap s ON a.symbol = s.symbol WHERE s.symbol IS NULL) AS missing
                """,
                [p],
            ).fetchdf()
            report["universe_active"] = int(uv.iloc[0]["active"])
            report["snapshot_symbols"] = int(uv.iloc[0]["snap"])
            report["missing_in_snapshot"] = int(uv.iloc[0]["missing"])

            short_df = con.execute(
                """
                WITH first_seen AS (
                    SELECT symbol, MIN(date) AS first_date FROM read_parquet(?) GROUP BY symbol
                ),
                active AS (
                    SELECT u.symbol,
                           year(COALESCE(u.listing_date, f.first_date, CAST(? AS DATE))) AS eff_y
                    FROM meta.universe u
                    LEFT JOIN first_seen f ON u.symbol = f.symbol
                    WHERE u.is_active = TRUE
                ),
                years AS (SELECT generate_series AS y FROM generate_series(year(CAST(? AS DATE)), year(CAST(? AS DATE)))),
                counts AS (
                    SELECT symbol, year(date) AS y, COUNT(*) AS cnt
                    FROM read_parquet(?)
                    WHERE date >= ? AND date <= ?
                    GROUP BY symbol, year(date)
                )
                SELECT y.y AS year
                FROM active a
                CROSS JOIN years y
                LEFT JOIN counts c ON a.symbol = c.symbol AND a.eff_y <= y.y AND y.y = c.y
                WHERE y.y >= a.eff_y AND COALESCE(c.cnt, 0) < ?
                """,
                [p, TARGET_START, TARGET_START, TARGET_END, p, TARGET_START, TARGET_END, MIN_ROWS_PER_YEAR],
            ).fetchdf()
            if not short_df.empty:
                by_year = short_df.groupby("year").size().reset_index(name="short_count")
                report["short_symbol_years_count"] = int(short_df.shape[0])
                report["short_symbol_years_by_year"] = by_year
            else:
                report["short_symbol_years_count"] = 0
                report["short_symbol_years_by_year"] = None

            for y, start, end in [(2016, "2016-01-01", "2016-12-31"), (2025, "2025-01-01", "2025-12-31")]:
                miss = con.execute(
                    """
                    WITH first_seen AS (SELECT symbol, MIN(date) AS first_date FROM read_parquet(?) GROUP BY symbol),
                         active AS (
                             SELECT u.symbol FROM meta.universe u
                             LEFT JOIN first_seen f ON u.symbol = f.symbol
                             WHERE u.is_active AND COALESCE(u.listing_date, f.first_date, CAST(? AS DATE)) <= CAST(? AS DATE)
                         ),
                         present AS (SELECT DISTINCT symbol FROM read_parquet(?) WHERE date >= ? AND date <= ?)
                    SELECT COUNT(*) AS cnt FROM active a
                    LEFT JOIN present p ON a.symbol = p.symbol WHERE p.symbol IS NULL
                    """,
                    [p, end, end, p, start, end],
                ).fetchdf()
                report["missing_2016" if y == 2016 else "missing_2025"] = int(miss.iloc[0]["cnt"])
    finally:
        con.close()
    return report


def format_report(
    release: dict,
    parquet_path: Path,
    actual_sha256: str,
    report: dict,
    meta_exists: bool,
    expected_sha256: str | None = None,
    sha256_ok: bool | None = None,
) -> str:
    lines = []
    lines.append("# 릴리즈 검증 리포트 (최신 Release)")
    lines.append("")
    lines.append(f"**생성 시각**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append(f"**Release 태그**: {release.get('tag_name', '')}")
    lines.append(f"**Release 일시**: {release.get('published_at', '')}")
    lines.append(f"**다운로드 파일**: {parquet_path.name}")
    lines.append(f"**실제 sha256**: `{actual_sha256}`")
    if expected_sha256:
        lines.append(f"**기대 sha256**: `{expected_sha256}`")
        lines.append(f"**sha256 일치**: {'✅ 일치' if sha256_ok else '❌ 불일치'}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 1. 요약")
    lines.append("")
    lines.append("| 항목 | 값 |")
    lines.append("|------|-----|")
    lines.append(f"| 총 행 수 | {report['total_rows']:,} |")
    lines.append(f"| 종목 수 | {report['symbols']:,} |")
    lines.append(f"| 최소일 | {report['min_date']} |")
    lines.append(f"| 최대일 | {report['max_date_str']} |")
    lines.append("")
    lines.append("## 2. 무결성")
    lines.append("")
    ok = report["integrity_ok"]
    lines.append(f"- 중복 (symbol, date): **{report['duplicate_keys']}**")
    lines.append(f"- 잘못된 가격/거래량 행: **{report['invalid_rows']}**")
    lines.append(f"- **무결성**: {'✅ 통과' if ok else '❌ 실패'}")
    lines.append("")
    lines.append("## 3. 최신 데이터 검증")
    lines.append("")
    days = report.get("days_behind")
    fresh = report.get("is_fresh")
    lines.append(f"- 스냅샷 **최대일**: {report['max_date_str']}")
    lines.append(f"- 오늘 기준 **경과 일수**: {days}일" if days is not None else "- (날짜 없음)")
    if days is not None:
        lines.append(f"- **최신성** ({MAX_DAYS_BEHIND_FOR_FRESH}일 이내): **{'✅ 최신' if fresh else '⚠️ 다소 오래됨'}**")
    lines.append(f"- 최대일 기준 **수집 종목 수**: {report['symbols_at_max_date']:,} / {report['symbols']:,}")
    lines.append("")
    lines.append("## 4. 연도별 커버리지")
    lines.append("")
    cov = report.get("coverage_by_year")
    if cov is not None and not cov.empty:
        lines.append("| 연도 | 종목 수 | 행 수 | 기간 |")
        lines.append("|------|--------|--------|------|")
        for _, r in cov.iterrows():
            lines.append(f"| {int(r['year'])} | {int(r['symbols']):,} | {int(r['rows']):,} | {r['y_min']} ~ {r['y_max']} |")
    lines.append("")
    lines.append("## 5. 완전성 (Universe 대비)")
    lines.append("")
    if meta_exists and report.get("universe_active") is not None:
        lines.append(f"- 활성 유니버스 종목 수: **{report['universe_active']:,}**")
        lines.append(f"- 스냅샷 종목 수: **{report['snapshot_symbols']:,}**")
        lines.append(f"- 스냅샷에 없는 종목 수: **{report['missing_in_snapshot']}**")
        lines.append(f"- Short symbol-years (연도당 {MIN_ROWS_PER_YEAR}행 미만): **{report.get('short_symbol_years_count', 0)}**")
        if report.get("short_symbol_years_by_year") is not None and not report["short_symbol_years_by_year"].empty:
            by_y = report["short_symbol_years_by_year"]
            parts = [f"{int(r['year'])}년 {int(r['short_count'])}건" for _, r in by_y.iterrows()]
            lines.append("  - 연도별: " + ", ".join(parts))
        lines.append(f"- 2016년 누락 종목 수: **{report.get('missing_2016', 'N/A')}**")
        lines.append(f"- 2025년 누락 종목 수: **{report.get('missing_2025', 'N/A')}**")
        lines.append("")
        complete = (
            report["missing_in_snapshot"] == 0
            and report.get("short_symbol_years_count", 0) == 0
            and report.get("missing_2016", 1) == 0
            and report.get("missing_2025", 1) == 0
        )
        lines.append(f"- **최근 정보 완전성**: **{'✅ 충족' if complete else '⚠️ 일부 부족'}**")
    else:
        lines.append("(meta.duckdb 없음 — Universe 대비 검증 생략)")
    lines.append("")
    lines.append("---")
    lines.append("")
    overall_ok = ok and (fresh if days is not None else True) and (sha256_ok if sha256_ok is not None else True)
    lines.append(f"**종합**: **{'✅ 검증 통과' if overall_ok else '⚠️ 항목 확인 필요'}**")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download latest release, validate freshness & completeness, and write report"
    )
    parser.add_argument(
        "--repo",
        default=DEFAULT_REPO,
        help=f"Repo owner/name or GitHub URL (default: {DEFAULT_REPO}). Example: {DEFAULT_RELEASES_URL}",
    )
    parser.add_argument("--tag", default=None, help="Use this release tag instead of latest (e.g. data-snapshot-20260317-1338)")
    parser.add_argument("--download-dir", type=Path, default=DOWNLOAD_DIR, help="Download directory")
    parser.add_argument("--report", type=Path, default=None, help="Save report to this file (default: print only)")
    args = parser.parse_args()

    print(f"GitHub token present: {bool(github_token())}")

    try:
        repo = parse_repo_from_url(args.repo)
        print(f"Normalized repo: {repo}")
    except ValueError as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    if args.tag:
        print(f"Fetching release by tag: {args.tag}...")
        release = get_release_by_tag(repo, args.tag.strip())
    else:
        print("Fetching latest release...")
        release = get_latest_release(repo)

    tag = release.get("tag_name", "")
    if not tag:
        print("No latest release found.")
        sys.exit(1)

    print(f"Latest release: {tag}")
    print(f"Normalized repo: {repo}")

    selected = select_assets(release)
    parquet_asset = selected["parquet"]
    sha_asset = selected["sha256"]
    json_asset = selected["json"]

    if not parquet_asset:
        assets = release.get("assets") or []
        print(f"No parquet asset. Assets: {[a.get('name') for a in assets]}")
        sys.exit(1)

    release_dir = args.download_dir / tag
    release_dir.mkdir(parents=True, exist_ok=True)

    parquet_path = release_dir / parquet_asset["name"]
    if not parquet_path.exists():
        print(f"Downloading parquet: {parquet_asset['name']} ...")
        download_asset_by_api(repo, parquet_asset, parquet_path)
        print(f"Saved: {parquet_path}")
    else:
        print(f"Using existing parquet: {parquet_path}")

    sha_path = None
    if sha_asset:
        sha_path = release_dir / sha_asset["name"]
        if not sha_path.exists():
            print(f"Downloading sha256: {sha_asset['name']} ...")
            download_asset_by_api(repo, sha_asset, sha_path)
            print(f"Saved: {sha_path}")
        else:
            print(f"Using existing sha256: {sha_path}")

    if json_asset:
        json_path = release_dir / json_asset["name"]
        if not json_path.exists():
            print(f"Downloading manifest json: {json_asset['name']} ...")
            download_asset_by_api(repo, json_asset, json_path)
            print(f"Saved: {json_path}")
        else:
            print(f"Using existing json: {json_path}")

    actual_sha256, expected_sha256, sha256_ok = verify_sha256_if_available(parquet_path, sha_path)

    meta_exists = META_DB.exists()
    report = run_validation(parquet_path, meta_exists)
    report["tag"] = tag

    text = format_report(
        release=release,
        parquet_path=parquet_path,
        actual_sha256=actual_sha256,
        report=report,
        meta_exists=meta_exists,
        expected_sha256=expected_sha256,
        sha256_ok=sha256_ok,
    )
    print(text)

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")
        print(f"\nReport saved: {args.report}")


if __name__ == "__main__":
    main()