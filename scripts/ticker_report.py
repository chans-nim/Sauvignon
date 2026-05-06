"""
티커(종목코드)를 입력하면 실제 수집된 데이터를 바탕으로 종목 리포트를 생성한다.

리포트 내용:
- 유니버스/수집 상태/릴리즈 스냅샷 저장 현황
- 최신 raw JSON 의 output1 실제 값 + 의미
- 최신 raw JSON 의 output2 필드 설명 + 최근 행 샘플 + 요약 통계
- 차트가 유용한 값은 PNG 로 생성

사용법 (프로젝트 루트에서):
  python -m scripts.ticker_report --symbol 005930
  python -m scripts.ticker_report --symbol 000540 --out-dir reports
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

if __name__ == "__main__" and str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common.settings import settings
from src.storage import meta_store
from scripts.sync_silver_from_github_release import http_latest_dataset_tag, is_dataset_release_tag

SILVER_DIR = settings.project_root / "data" / "lake" / "silver" / "ohlcv_daily"
RAW_OHLCV_DIR = settings.project_root / "data" / "raw" / "ohlcv"
TARGET_START = "2016-01-01"
# 연도별 커버리지/차트는 현재 연도까지 포함 (최신 데이터가 제대로 반영되도록)
TARGET_END = f"{date.today().year}-12-31"
MIN_ROWS_PER_YEAR = 200
RECENT_MONTHS = 3

MANIFEST_PATH = settings.project_root / "data_manifest.json"
DOWNLOAD_DIR = settings.project_root / "data" / "downloads"
SNAPSHOT_DIR = settings.project_root / "data" / "snapshot"
DELTA_DIR = settings.project_root / "data" / "delta"


OUTPUT1_FIELDS = {
    "hts_kor_isnm": ("종목명", "현재 조회 종목의 한글명"),
    "stck_shrn_iscd": ("종목코드", "단축 종목코드"),
    "stck_prpr": ("현재가", "조회 시점 현재가"),
    "prdy_vrss": ("전일 대비", "전일 종가 대비 등락 금액"),
    "prdy_vrss_sign": ("대비 부호", "1=상한, 2=상승, 3=보합, 4=하한, 5=하락"),
    "prdy_ctrt": ("등락률", "전일 종가 대비 등락률(%)"),
    "stck_prdy_clpr": ("전일 종가", "이전 거래일 종가"),
    "stck_oprc": ("시가", "당일 시가"),
    "stck_hgpr": ("고가", "당일 고가"),
    "stck_lwpr": ("저가", "당일 저가"),
    "acml_vol": ("누적 거래량", "당일 누적 거래량"),
    "acml_tr_pbmn": ("누적 거래대금", "당일 누적 거래대금"),
    "prdy_vol": ("전일 거래량", "이전 거래일 거래량"),
    "prdy_vrss_vol": ("전일 대비 거래량 증감", "전일 거래량 대비 증감"),
    "vol_tnrt": ("거래량 회전율", "상장주식수 대비 거래량 비율(%)"),
    "askp": ("매도호가", "조회 시점 최우선 매도호가"),
    "bidp": ("매수호가", "조회 시점 최우선 매수호가"),
    "stck_mxpr": ("상한가", "당일 가격제한 상한"),
    "stck_llam": ("하한가", "당일 가격제한 하한"),
    "stck_fcam": ("액면가", "주식 액면가"),
    "lstn_stcn": ("상장주식수", "상장 주식 수"),
    "cpfn": ("자본금", "자본금"),
    "hts_avls": ("시가총액", "HTS 기준 시가총액"),
    "per": ("PER", "주가수익비율"),
    "eps": ("EPS", "주당순이익"),
    "pbr": ("PBR", "주가순자산비율"),
    "itewhol_loan_rmnd_ratem name": ("대주잔고비율", "대주잔고 비율"),
}

OUTPUT2_FIELDS = {
    "stck_bsop_date": ("영업일자", "해당 일봉의 거래일"),
    "stck_clpr": ("종가", "해당 거래일 종가"),
    "stck_oprc": ("시가", "해당 거래일 시가"),
    "stck_hgpr": ("고가", "해당 거래일 고가"),
    "stck_lwpr": ("저가", "해당 거래일 저가"),
    "acml_vol": ("거래량", "해당 거래일 거래량"),
    "acml_tr_pbmn": ("거래대금", "해당 거래일 거래대금"),
    "prdy_vrss": ("전일 대비", "전일 종가 대비 변동 금액"),
    "prdy_vrss_sign": ("대비 부호", "1=상한, 2=상승, 3=보합, 4=하한, 5=하락"),
    "flng_cls_code": ("락 구분 코드", "권리락/배당락 등 구분 코드"),
    "prtt_rate": ("분할 비율", "분할/병합 비율 관련 값"),
    "mod_yn": ("수정 여부", "수정주가 적용 여부"),
    "revl_issu_reas": ("재평가 사유", "재평가/정정 관련 사유"),
}


def fmt_num(value) -> str:
    if value is None or value == "":
        return "-"
    try:
        if isinstance(value, str) and "." in value:
            return f"{float(value):,.2f}"
        return f"{int(value):,}"
    except Exception:
        return str(value)


def fmt_pct(value) -> str:
    if value is None or value == "":
        return "-"
    try:
        return f"{float(value):.2f}%"
    except Exception:
        return str(value)


def format_output_value(key: str, value) -> str:
    if key in {"prdy_ctrt", "vol_tnrt", "per", "pbr", "prtt_rate", "itewhol_loan_rmnd_ratem name"}:
        return fmt_pct(value) if key != "per" and key != "pbr" else str(value)
    if key in {"hts_kor_isnm", "stck_shrn_iscd", "prdy_vrss_sign", "flng_cls_code", "mod_yn", "revl_issu_reas"}:
        return str(value)
    if key == "stck_bsop_date":
        return str(value)
    return fmt_num(value)


def get_universe_row(symbol: str):
    meta_store.ensure_tables()
    con = meta_store.connect()
    row = con.execute(
        """
        SELECT symbol, std_code, name, market, asset_type, listing_date,
               is_etf, is_spac, is_trading_halt, is_admin_issue,
               is_warning, is_active, updated_at
        FROM universe
        WHERE symbol = ?
        """,
        [symbol],
    ).fetchone()
    con.close()
    if not row:
        return None
    return {
        "symbol": row[0],
        "std_code": row[1],
        "name": row[2],
        "market": row[3],
        "asset_type": row[4],
        "listing_date": row[5],
        "is_etf": row[6],
        "is_spac": row[7],
        "is_trading_halt": row[8],
        "is_admin_issue": row[9],
        "is_warning": row[10],
        "is_active": row[11],
        "updated_at": row[12],
    }


def get_collect_state(symbol: str):
    con = meta_store.connect()
    row = con.execute(
        """
        SELECT last_success_date, last_attempt_at, retry_count, last_error, updated_at
        FROM collect_state
        WHERE symbol = ? AND timeframe = '1d'
        """,
        [symbol],
    ).fetchone()
    con.close()
    if not row:
        return None
    return {
        "last_success_date": row[0],
        "last_attempt_at": row[1],
        "retry_count": row[2],
        "last_error": row[3],
        "updated_at": row[4],
    }


def load_silver(symbol: str, market: str) -> pd.DataFrame:
    import duckdb

    base = SILVER_DIR / f"market={market}" / f"symbol={symbol}"
    paths = [p.as_posix() for p in base.rglob("data.parquet")]
    if not paths:
        return pd.DataFrame()
    con = duckdb.connect()
    df = con.execute("SELECT * FROM read_parquet(?) ORDER BY date", [paths]).fetchdf()
    con.close()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    return df.sort_values("date").reset_index(drop=True)


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


def manifest_latest_current_entry(path: Path = MANIFEST_PATH) -> dict | None:
    """data_manifest.json 의 latest_current 객체 (tag, created_at, max_date 등)."""
    if not path.exists():
        return None
    m = json.loads(path.read_text(encoding="utf-8"))
    lc = m.get("latest_current")
    return lc if isinstance(lc, dict) else None


def query_symbol_last_day_max_ingested(parquet_path: Path, symbol: str, market: str) -> str | None:
    """해당 종목의 최종 거래일(date=max) 행들 중 ingested_at 최대값 (컬럼 없으면 None)."""
    import duckdb

    con = duckdb.connect()
    try:
        row = con.execute(
            """
            WITH t AS (
              SELECT date, ingested_at
              FROM read_parquet(?)
              WHERE symbol = ? AND market = ?
            )
            SELECT MAX(ingested_at)::VARCHAR AS mx
            FROM t
            WHERE date = (SELECT MAX(date) FROM t)
            """,
            [parquet_path.as_posix(), symbol, market],
        ).fetchone()
    except Exception:
        return None
    finally:
        con.close()
    if not row or row[0] is None:
        return None
    s = str(row[0]).strip()
    return s or None


def _gh_subprocess_env() -> dict:
    env = os.environ.copy()
    pat = os.getenv("GH_PAT_SAUVIGNON") or ""
    if pat:
        env["GH_TOKEN"] = pat
    return env


def release_tag_sort_key(tag: str) -> tuple:
    """data-snapshot-YYYYMMDD-HHMM 등 태그를 비교 가능한 튜플로 변환."""
    m = re.match(r"^data-(?:snapshot|full|delta)-(\d{8})-(\d{4})$", tag)
    if m:
        return (0, m.group(1), m.group(2))
    return (1, tag, "")


def _gh_latest_dataset_tag_optional_with_pat(repo: str) -> str | None:
    """gh 호출 시 GH_PAT_SAUVIGNON 을 GH_TOKEN 으로 넘기는 ticker_report 전용."""
    r = parse_repo_from_url(repo)
    try:
        out = subprocess.check_output(
            [
                "gh",
                "release",
                "list",
                "--repo",
                r,
                "--limit",
                "100",
                "--exclude-drafts",
                "--json",
                "tagName",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
            env=_gh_subprocess_env(),
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError, OSError):
        return None
    if not out:
        return None
    try:
        for row in json.loads(out):
            if not isinstance(row, dict):
                continue
            t = str(row.get("tagName") or "").strip()
            if is_dataset_release_tag(t):
                return t
    except (json.JSONDecodeError, TypeError):
        return None
    return None


def remote_latest_release_tag(repo: str) -> str | None:
    """data-snapshot|full|delta 릴리즈만 후보로 삼는다 (thema-sector-* 등 제외)."""
    r = parse_repo_from_url(repo)
    return _gh_latest_dataset_tag_optional_with_pat(r) or http_latest_dataset_tag(r)


def resolve_default_release_tag(repo: str) -> tuple[str, str | None, bool]:
    """
    manifest와 GitHub 최신 릴리즈 중 더 새 태그를 고른다.
    Returns: (resolved_tag, manifest_tag_from_file_or_none, chose_github_over_manifest)
    """
    manifest_tag = load_manifest_tag()
    gh_tag = remote_latest_release_tag(repo)
    if manifest_tag and gh_tag:
        if release_tag_sort_key(gh_tag) > release_tag_sort_key(manifest_tag):
            return gh_tag, manifest_tag, True
        return manifest_tag, manifest_tag, False
    if manifest_tag:
        return manifest_tag, manifest_tag, False
    if gh_tag:
        return gh_tag, None, False
    raise SystemExit(
        "Could not resolve release tag: data_manifest.json has no latest_current and "
        "no data-(snapshot|full|delta)-* release found via gh/API (other tags are skipped). "
        "Use --release-tag or check GH_PAT_SAUVIGNON / gh auth."
    )


def resolve_local_release_parquet(tag: str) -> Path | None:
    if tag.startswith("data-delta-"):
        base = DELTA_DIR
    elif tag.startswith("data-snapshot-") or tag.startswith("data-full-"):
        base = SNAPSHOT_DIR
    else:
        return None
    p = base / f"{tag}.parquet"
    return p if p.exists() else None


def download_release_parquet(repo: str, tag: str, out_dir: Path) -> Path:
    repo = parse_repo_from_url(repo)
    release_dir = out_dir / tag
    release_dir.mkdir(parents=True, exist_ok=True)

    # 이전 다운로드 찌꺼기 방지(동일 tag이면 파일명이 동일하므로 기본적으로 유효하지만, 안전하게 정리)
    for old in release_dir.glob("*.parquet"):
        old.unlink(missing_ok=True)

    try:
        gh_pat = os.getenv("GH_PAT_SAUVIGNON") or ""
        if not gh_pat:
            raise SystemExit("Missing GH_PAT_SAUVIGNON for gh release download")
        gh_env = os.environ.copy()
        gh_env["GH_TOKEN"] = gh_pat
        subprocess.run(
            ["gh", "release", "download", tag, "--repo", repo, "-D", str(release_dir), "-p", "*.parquet"],
            check=True,
            env=gh_env,
        )
    except FileNotFoundError:
        # Local 환경에 gh CLI가 없는 경우를 위해 HTTP 다운로드로 폴백
        return download_release_parquet_http(repo, tag, release_dir)

    parquets = sorted(release_dir.glob("*.parquet"))
    if not parquets:
        raise SystemExit(f"No parquet asset downloaded for {repo}@{tag}")

    expected = release_dir / f"{tag}.parquet"
    if expected.exists():
        return expected
    # fallback: 하나만 있으면 그걸, 여러 개면 정렬 첫 파일
    return parquets[0]


def download_release_parquet_http(repo: str, tag: str, release_dir: Path) -> Path:
    """
    gh CLI 없이도 GitHub API + HTTP로 릴리즈 parquet을 직접 다운로드한다.
    """
    import requests

    # 요청 사항: GitHub 토큰은 GH_PAT_SAUVIGNON 우선 사용
    token = os.getenv("GH_PAT_SAUVIGNON") or os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN") or ""

    headers = {
        "X-GitHub-Api-Version": "2022-11-28",
        "Accept": "application/vnd.github+json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    api_url = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    try:
        payload = requests.get(api_url, headers=headers, timeout=30).json()
    except Exception as e:
        raise SystemExit(f"GitHub API request failed. repo={repo} tag={tag}. err={e}") from e

    assets = payload.get("assets") or []
    parquet_assets = [a for a in assets if str(a.get("name") or "").endswith(".parquet")]
    if not parquet_assets:
        asset_names = [a.get("name") for a in assets]
        payload_msg = payload.get("message") if isinstance(payload, dict) else None
        raise SystemExit(
            f"No parquet assets found in {repo}@{tag}. "
            f"payload_message={payload_msg}. assets={asset_names}"
        )

    expected_name = f"{tag}.parquet"
    chosen = None
    for a in parquet_assets:
        if a.get("name") == expected_name:
            chosen = a
            break
    chosen = chosen or parquet_assets[0]

    asset_id = chosen.get("id")
    if not asset_id:
        raise SystemExit(f"Missing asset id for {chosen.get('name')} in {repo}@{tag}")

    dst = release_dir / str(chosen.get("name") or f"{tag}.parquet")
    if dst.exists():
        return dst

    dst.parent.mkdir(parents=True, exist_ok=True)

    asset_url = f"https://api.github.com/repos/{repo}/releases/assets/{asset_id}"
    binary_headers = {
        "X-GitHub-Api-Version": "2022-11-28",
        "Accept": "application/octet-stream",
    }
    if token:
        binary_headers["Authorization"] = f"Bearer {token}"

    with requests.get(asset_url, headers=binary_headers, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(dst, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                if chunk:
                    f.write(chunk)

    return dst


def load_release_series(
    symbol: str,
    market: str,
    *,
    repo: str,
    tag: str | None,
    download_dir: Path,
    prefer_manifest: bool = False,
) -> tuple[pd.DataFrame, dict]:
    """
    GitHub Release 스냅샷 parquet에서 해당 종목 OHLCV 시계열 + 수집·릴리즈 메타를 반환한다.

    기본(--release-tag 없음): 로컬 data_manifest.json 과 GitHub의 최신 data-(snapshot|full|delta)-* 태그 중
    타임스탬프가 더 새 쪽을 사용한다 (thema-sector-* 등 비데이터셋 릴리즈는 후보에서 제외).
    """
    import duckdb

    repo = parse_repo_from_url(repo)
    manifest_file_tag = load_manifest_tag()
    chose_gh_over_manifest = False
    if tag:
        resolved_tag = tag
    elif prefer_manifest:
        resolved_tag = manifest_file_tag or remote_latest_release_tag(repo)
        if not resolved_tag:
            raise SystemExit(
                "--prefer-manifest 인데 data_manifest.json 에 latest_current 가 없고, "
                "GitHub 최신 릴리즈도 조회할 수 없습니다."
            )
    else:
        resolved_tag, _mf, chose_gh_over_manifest = resolve_default_release_tag(repo)

    if chose_gh_over_manifest and manifest_file_tag:
        print(
            f"[ticker_report] note: data_manifest.json latest_current={manifest_file_tag} "
            f"is older than GitHub latest; using release {resolved_tag}."
        )

    m_entry = manifest_latest_current_entry()
    manifest_created_at = (m_entry or {}).get("created_at")
    manifest_max_date = (m_entry or {}).get("max_date")

    local_parquet = resolve_local_release_parquet(resolved_tag)
    downloaded = False
    if local_parquet is None:
        local_parquet = download_release_parquet(repo, resolved_tag, download_dir)
        downloaded = True
    print(f"[ticker_report] release tag={resolved_tag} parquet={local_parquet} ({'downloaded' if downloaded else 'cached'})")

    con = duckdb.connect()
    try:
        df = con.execute(
            """
            SELECT
              date, open, high, low, close, volume, value
            FROM read_parquet(?)
            WHERE symbol = ? AND market = ?
            ORDER BY date
            """,
            [local_parquet.as_posix(), symbol, market],
        ).fetchdf()
    finally:
        con.close()

    last_ingested = query_symbol_last_day_max_ingested(local_parquet, symbol, market)
    meta = {
        "release_tag": resolved_tag,
        "manifest_file_tag": manifest_file_tag,
        "used_newer_github_release_than_manifest": chose_gh_over_manifest,
        "parquet_path": str(local_parquet),
        "manifest_created_at": str(manifest_created_at) if manifest_created_at else None,
        "manifest_max_date": str(manifest_max_date) if manifest_max_date else None,
        "symbol_last_day_max_ingested_at": last_ingested,
    }

    if df.empty:
        return df, meta
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    return df.sort_values("date").reset_index(drop=True), meta


def latest_raw_file(symbol: str) -> Path | None:
    candidates = []
    if not RAW_OHLCV_DIR.exists():
        return None
    for day_dir in RAW_OHLCV_DIR.iterdir():
        if not day_dir.is_dir():
            continue
        for f in day_dir.glob(f"{symbol}*.json"):
            candidates.append(f)
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: (p.parent.name, p.name))[-1]


def raw_files_for_symbol(symbol: str) -> list[Path]:
    candidates = []
    if not RAW_OHLCV_DIR.exists():
        return candidates
    for day_dir in RAW_OHLCV_DIR.iterdir():
        if not day_dir.is_dir():
            continue
        for f in day_dir.glob(f"{symbol}*.json"):
            candidates.append(f)
    return sorted(candidates, key=lambda p: (p.parent.name, p.name))


def load_latest_raw(symbol: str):
    p = latest_raw_file(symbol)
    if p is None:
        return None, None
    data = json.loads(p.read_text(encoding="utf-8"))
    return p, data


def load_output2_recent_six_months(symbol: str) -> pd.DataFrame:
    rows: list[dict] = []
    for p in raw_files_for_symbol(symbol):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        payload_rows = data.get("output2") or []
        if isinstance(payload_rows, list):
            rows.extend(payload_rows)
    df = normalize_output2_rows(rows)
    if df.empty:
        return df
    df = df.drop_duplicates(subset=["stck_bsop_date"], keep="last").sort_values("date").reset_index(drop=True)
    latest_date = df["date"].max()
    cutoff = (latest_date - pd.DateOffset(months=6)).normalize()
    return df[df["date"] >= cutoff].copy().reset_index(drop=True)


def normalize_output2_rows(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if "stck_bsop_date" in df.columns:
        df["date"] = pd.to_datetime(df["stck_bsop_date"], format="%Y%m%d", errors="coerce")
    for col in ["stck_clpr", "stck_oprc", "stck_hgpr", "stck_lwpr", "acml_vol", "acml_tr_pbmn", "prdy_vrss"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.sort_values("date").reset_index(drop=True)


def coverage_summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"total": 0, "years": {}, "missing": [], "short": []}
    sub = df[(df["date"] >= TARGET_START) & (df["date"] <= TARGET_END)].copy()
    years = sub["date"].dt.year.value_counts().sort_index().to_dict() if not sub.empty else {}
    missing = []
    short = []
    for y in range(int(TARGET_START[:4]), int(TARGET_END[:4]) + 1):
        cnt = years.get(y, 0)
        if cnt == 0:
            missing.append(y)
        elif cnt < MIN_ROWS_PER_YEAR:
            short.append((y, cnt))
    return {"total": len(sub), "years": years, "missing": missing, "short": short}


def build_output1_rows(output1: dict) -> list[tuple[str, str, str, str]]:
    rows = []
    for key, value in output1.items():
        label, meaning = OUTPUT1_FIELDS.get(key, (key, "필드 설명 미등록"))
        rows.append((key, label, format_output_value(key, value), meaning))
    return rows


def build_output2_field_rows(output2_df: pd.DataFrame) -> list[tuple[str, str, str, str]]:
    rows = []
    sample = output2_df.iloc[-1].to_dict() if not output2_df.empty else {}
    for key in sample.keys():
        if key == "date":
            continue
        label, meaning = OUTPUT2_FIELDS.get(key, (key, "필드 설명 미등록"))
        rows.append((key, label, format_output_value(key, sample.get(key)), meaning))
    return rows


def render_html_table(headers: list[str], rows: list[list[str]]) -> str:
    parts = ['<table class="data-table">', "<thead><tr>"]
    parts.extend([f"<th>{html.escape(str(h))}</th>" for h in headers])
    parts.append("</tr></thead><tbody>")
    for row in rows:
        parts.append("<tr>")
        parts.extend([f"<td>{html.escape(str(x).replace(chr(10), ' '))}</td>" for x in row])
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def plot_silver_overview(df: pd.DataFrame, symbol: str, out_path: Path) -> None:
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 7), sharex=True, height_ratios=[3, 1])
    x = df["date"]
    ax1.plot(x, df["close"], color="steelblue", linewidth=1)
    ax1.set_title(f"{symbol} Silver Long-term Overview")
    ax1.set_ylabel("Close")
    ax1.grid(True, alpha=0.3)
    ax2.bar(x, df["volume"] / 1e6, color="gray", alpha=0.7, width=2)
    ax2.set_ylabel("Volume (M)")
    ax2.grid(True, alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax2.xaxis.set_major_locator(mdates.YearLocator())
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()


def plot_recent_silver(df: pd.DataFrame, symbol: str, out_path: Path, *, months: int = RECENT_MONTHS) -> None:
    """
    최근 N개월 Release snapshot 기준 차트.
    raw(output2) 대신 Release snapshot 기반으로 최신 구간을 확인할 수 있게 해줌.
    """
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    if df.empty:
        return

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 7), sharex=True, height_ratios=[3, 1])
    x = df["date"]
    ax1.plot(x, df["close"], color="steelblue", linewidth=1.2, label="Close")
    if {"low", "high"}.issubset(df.columns):
        ax1.fill_between(x, df["low"], df["high"], color="steelblue", alpha=0.12, label="Low-High")
    ax1.set_title(f"{symbol} Recent {months}M (Silver)")
    ax1.set_ylabel("Price")
    ax1.legend(loc="upper left")
    ax1.grid(True, alpha=0.3)

    ax2.bar(x, df["volume"] / 1e6, color="gray", alpha=0.7, width=2)
    ax2.set_ylabel("Volume (M)")
    ax2.grid(True, alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()


def plot_raw_output2(df: pd.DataFrame, symbol: str, out_path: Path) -> None:
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    fig, axes = plt.subplots(3, 1, figsize=(14, 9), sharex=True, height_ratios=[3, 1, 1])
    x = df["date"]
    axes[0].plot(x, df["stck_clpr"], color="tab:blue", linewidth=1.2, label="Close")
    axes[0].plot(x, df["stck_oprc"], color="tab:orange", linewidth=0.8, alpha=0.7, label="Open")
    axes[0].fill_between(x, df["stck_lwpr"], df["stck_hgpr"], color="tab:blue", alpha=0.12, label="Low-High")
    axes[0].set_title(f"{symbol} Latest Raw output2 Window")
    axes[0].set_ylabel("Price")
    axes[0].legend(loc="upper left")
    axes[0].grid(True, alpha=0.3)
    axes[1].bar(x, df["acml_vol"] / 1e6, color="gray", alpha=0.7, width=2)
    axes[1].set_ylabel("Vol (M)")
    axes[1].grid(True, alpha=0.3)
    axes[2].bar(x, df["acml_tr_pbmn"] / 1e9, color="tab:green", alpha=0.7, width=2)
    axes[2].set_ylabel("Value (B KRW)")
    axes[2].grid(True, alpha=0.3)
    axes[2].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()


def plot_yearly_rows(coverage: dict, symbol: str, out_path: Path) -> None:
    import matplotlib.pyplot as plt

    years = list(range(int(TARGET_START[:4]), int(TARGET_END[:4]) + 1))
    counts = [coverage["years"].get(y, 0) for y in years]
    colors = ["tab:blue" if c >= MIN_ROWS_PER_YEAR else "tab:red" for c in counts]
    plt.figure(figsize=(10, 4))
    plt.bar([str(y) for y in years], counts, color=colors, alpha=0.8)
    plt.axhline(MIN_ROWS_PER_YEAR, color="black", linestyle="--", linewidth=1, label=f"threshold={MIN_ROWS_PER_YEAR}")
    plt.title(f"{symbol} Rows per Year ({TARGET_START[:4]}-{TARGET_END[:4]})")
    plt.ylabel("Rows")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()


def build_report(
    symbol: str,
    universe: dict,
    state: dict | None,
    silver_df: pd.DataFrame,
    raw_path: Path | None,
    raw_data: dict | None,
    out_dir: Path,
    *,
    release_source_meta: dict | None = None,
) -> str:
    report_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    coverage = coverage_summary(silver_df)
    output1 = {} if not raw_data else raw_data.get("output1") or {}
    output2_rows = [] if not raw_data else raw_data.get("output2") or []
    output2_df = normalize_output2_rows(output2_rows)
    output2_recent_df = load_output2_recent_six_months(symbol)

    # 최근 구간은 raw이 아니라 Release snapshot 기준으로 표시
    recent_silver_df = pd.DataFrame()
    if not silver_df.empty:
        latest_date = silver_df["date"].max()
        cutoff = (latest_date - pd.DateOffset(months=RECENT_MONTHS)).normalize()
        recent_silver_df = silver_df[silver_df["date"] >= cutoff].copy()

    silver_chart = out_dir / f"{symbol}_silver_overview.png"
    raw_chart = out_dir / f"{symbol}_recent_{RECENT_MONTHS}m_silver.png"
    year_chart = out_dir / f"{symbol}_yearly_rows.png"
    if not silver_df.empty:
        plot_silver_overview(silver_df, symbol, silver_chart)
        plot_yearly_rows(coverage, symbol, year_chart)
    if not recent_silver_df.empty:
        plot_recent_silver(recent_silver_df, symbol, raw_chart, months=RECENT_MONTHS)

    output1_table = render_html_table(
        ["필드코드", "표시명", "실제값", "의미"],
        [[a, b, c, d] for a, b, c, d in build_output1_rows(output1)],
    ) if output1 else '<p class="empty">latest raw output1 없음</p>'

    output2_field_table = render_html_table(
        ["필드코드", "표시명", "최근 샘플값", "의미"],
        [[a, b, c, d] for a, b, c, d in build_output2_field_rows(output2_recent_df)],
    ) if not output2_recent_df.empty else '<p class="empty">최근 6개월 output2 없음</p>'

    recent_rows_html = '<p class="empty">recent rows 없음</p>'
    if not recent_silver_df.empty:
        tail = recent_silver_df.tail(10).copy()
        tail["date"] = tail["date"].dt.strftime("%Y-%m-%d")
        recent_rows_html = render_html_table(
            ["date", "open", "high", "low", "close", "volume", "value"],
            [
                [
                    r["date"],
                    fmt_num(r.get("open")),
                    fmt_num(r.get("high")),
                    fmt_num(r.get("low")),
                    fmt_num(r.get("close")),
                    fmt_num(r.get("volume")),
                    fmt_num(r.get("value")),
                ]
                for _, r in tail.iterrows()
            ],
        )
    release_hero_lines = ""
    if release_source_meta:
        rt = release_source_meta.get("release_tag") or "-"
        mc = release_source_meta.get("manifest_created_at") or "-"
        mm = release_source_meta.get("manifest_max_date") or "-"
        li = release_source_meta.get("symbol_last_day_max_ingested_at") or "-"
        release_hero_lines = (
            f'<div>릴리즈 스냅샷 태그: {html.escape(str(rt))}</div>'
            f'<div>manifest 생성 시각(UTC): {html.escape(str(mc))}</div>'
            f'<div>manifest 최대 거래일: {html.escape(str(mm))}</div>'
            f'<div>본 종목 최종일 ingested_at 최대: {html.escape(str(li))}</div>'
        )
        mft = release_source_meta.get("manifest_file_tag")
        if release_source_meta.get("used_newer_github_release_than_manifest") and mft:
            release_hero_lines += (
                '<div class="note">로컬 <code>data_manifest.json</code>의 '
                f"<code>latest_current</code> 태그({html.escape(str(mft))})보다 GitHub 최신 릴리즈가 "
                f"새로워 <strong>{html.escape(str(rt))}</strong>를 사용했습니다.</div>"
            )

    summary_table = render_html_table(
        ["항목", "값", "의미"],
        [
            ["symbol", universe.get("symbol", "-"), "조회 대상 종목코드"],
            ["name", universe.get("name", "-"), "유니버스에 저장된 종목명"],
            ["market", universe.get("market", "-"), "시장 구분"],
            ["is_active", universe.get("is_active", "-"), "현재 유니버스 활성 여부"],
            ["is_etf", universe.get("is_etf", "-"), "ETF 여부"],
            ["is_spac", universe.get("is_spac", "-"), "스팩 여부"],
            ["is_warning", universe.get("is_warning", "-"), "투자경고/주의 관련 플래그"],
            ["last_success_date", "-" if not state else state.get("last_success_date", "-"), "마지막 성공 거래일"],
            ["last_attempt_at", "-" if not state else state.get("last_attempt_at", "-"), "마지막 수집 시도 시각"],
            ["retry_count", "-" if not state else state.get("retry_count", "-"), "실패 후 재시도 횟수"],
            ["last_error", "-" if not state else (state.get("last_error") or "-"), "최근 실패 오류 메시지"],
        ],
    )

    release_meta_block = ""
    if release_source_meta:
        release_meta_block = (
            "<h3>2-0. 릴리즈·수집 메타</h3>"
            "<p>동일 거래일(date)은 이후 수집분이 이전분을 덮어씁니다. "
            "KST 20:00 이전에 수집된 당일 봉은 장 마감 후 재수집으로 갱신될 수 있습니다.</p>"
            + render_html_table(
                ["항목", "값", "의미"],
                [
                    ["release_tag", str(release_source_meta.get("release_tag") or "-"), "사용한 GitHub Release 태그"],
                    [
                        "manifest_file_tag",
                        str(release_source_meta.get("manifest_file_tag") or "-"),
                        "로컬 data_manifest.json latest_current.tag (실제 사용 태그와 다를 수 있음)",
                    ],
                    [
                        "used_newer_github_than_manifest",
                        str(release_source_meta.get("used_newer_github_release_than_manifest") or False),
                        "GitHub 최신 릴리즈가 manifest보다 새로 선택됨 여부",
                    ],
                    ["manifest_created_at", str(release_source_meta.get("manifest_created_at") or "-"), "data_manifest 기준 스냅샷 반영 시각(UTC)"],
                    ["manifest_max_date", str(release_source_meta.get("manifest_max_date") or "-"), "스냅샷에 포함된 최대 거래일"],
                    [
                        "symbol_last_day_max_ingested_at",
                        str(release_source_meta.get("symbol_last_day_max_ingested_at") or "-"),
                        "본 종목 최종 거래일 행의 ingested_at 최대(병합 시각 추정)",
                    ],
                    ["parquet_path", str(release_source_meta.get("parquet_path") or "-"), "읽은 스냅샷 parquet 경로"],
                ],
            )
        )

    silver_section = '<p class="empty">릴리즈 스냅샷 데이터가 없습니다.</p>'
    if not silver_df.empty:
        min_date = silver_df["date"].min().date()
        max_date = silver_df["date"].max().date()
        silver_stats = render_html_table(
            ["항목", "값", "의미"],
            [
                ["rows_total", fmt_num(len(silver_df)), "릴리즈 스냅샷에 저장된 전체 일봉 행 수"],
                ["date_range", f"{min_date} ~ {max_date}", "실제 저장 구간"],
                ["rows_in_target", fmt_num(coverage["total"]), f"{TARGET_START} ~ {TARGET_END} 구간 행 수"],
                ["missing_years", ", ".join(map(str, coverage["missing"])) or "-", "해당 연도 데이터가 전혀 없는 경우"],
                ["short_years", ", ".join([f"{y}:{c}" for y, c in coverage["short"]]) or "-", f"{MIN_ROWS_PER_YEAR}행 미만 연도"],
            ],
        )
        year_rows = [
            [str(y), fmt_num(coverage["years"].get(y, 0)), "ok" if coverage["years"].get(y, 0) >= MIN_ROWS_PER_YEAR else "SHORT" if coverage["years"].get(y, 0) > 0 else "MISSING"]
            for y in range(int(TARGET_START[:4]), int(TARGET_END[:4]) + 1)
        ]
        yearly_table = render_html_table(["연도", "행 수", "판정"], year_rows)
        silver_section = (
            f"{release_meta_block}"
            f"{silver_stats}"
            f'<div class="chart-grid"><figure><img src="{html.escape(silver_chart.name)}" alt="silver overview"></figure>'
            f'<figure><img src="{html.escape(year_chart.name)}" alt="yearly rows"></figure></div>'
            f"{yearly_table}"
        )
    elif release_meta_block:
        silver_section = release_meta_block + silver_section

    recent_silver_stats_html = '<p class="empty">최근 Release snapshot 요약 통계 없음</p>'
    if not recent_silver_df.empty:
        recent_silver_stats_html = render_html_table(
            ["항목", "값", "의미"],
            [
                [
                    "window_range",
                    f"{recent_silver_df['date'].min().date()} ~ {recent_silver_df['date'].max().date()}",
                    f"최근 {RECENT_MONTHS}개월 Release snapshot 구간",
                ],
                ["rows", fmt_num(len(recent_silver_df)), f"최근 {RECENT_MONTHS}개월 구간 행 수"],
                ["close_min", fmt_num(recent_silver_df["close"].min()), "종가 최저값"],
                ["close_max", fmt_num(recent_silver_df["close"].max()), "종가 최고값"],
                ["close_mean", fmt_num(round(recent_silver_df["close"].mean())), "종가 평균"],
                ["volume_mean", fmt_num(round(recent_silver_df["volume"].mean())), "거래량 평균"],
                ["value_mean", fmt_num(round(recent_silver_df["value"].mean())), "거래대금 평균"],
            ],
        )

    silver_recent_field_table = '<p class="empty">최근 Release snapshot 샘플 값 없음</p>'
    if not recent_silver_df.empty:
        last = recent_silver_df.iloc[-1]
        last_date = last["date"].date() if hasattr(last["date"], "date") else last["date"]
        silver_recent_field_table = render_html_table(
            ["필드코드", "표시명", "최근 샘플값", "의미"],
            [
                ["date", "영업일자", str(last_date), f"최근 {RECENT_MONTHS}개월 Release snapshot 마지막 거래일"],
                ["open", "시가", fmt_num(last.get("open")), "해당 거래일 시가"],
                ["high", "고가", fmt_num(last.get("high")), "해당 거래일 고가"],
                ["low", "저가", fmt_num(last.get("low")), "해당 거래일 저가"],
                ["close", "종가", fmt_num(last.get("close")), "해당 거래일 종가"],
                ["volume", "거래량", fmt_num(last.get("volume")), "해당 거래일 거래량"],
                ["value", "거래대금", fmt_num(last.get("value")), "해당 거래일 거래대금"],
            ],
        )
    recent_silver_block_html = recent_silver_stats_html
    if not recent_silver_df.empty:
        recent_silver_block_html = (
            f'<h3>3-2. 최근 {RECENT_MONTHS}개월 Release snapshot 샘플(마지막 거래일)</h3>'
            + silver_recent_field_table
            + f'<h3>3-3. 최근 {RECENT_MONTHS}개월 Release snapshot 샘플</h3>'
            + recent_rows_html
            + f'<h3>3-4. 최근 {RECENT_MONTHS}개월 Release snapshot 요약 통계</h3>'
            + recent_silver_stats_html
            + f'<div class="chart-grid"><figure><img src="{html.escape(raw_chart.name)}" alt="recent silver chart"></figure></div>'
        )
    raw_section = recent_silver_block_html
    if raw_path is not None and raw_data is not None:
        raw_stats = render_html_table(
            ["항목", "값", "의미"],
            [
                ["file_path", str(raw_path), "가장 최신 raw JSON 파일 경로"],
                ["rt_cd", raw_data.get("rt_cd", "-"), "API 결과 코드 (0/0000 정상)"],
                ["msg_cd", raw_data.get("msg_cd", "-"), "API 메시지 코드"],
                ["msg1", raw_data.get("msg1", "-"), "API 응답 메시지"],
                ["output2_rows", fmt_num(len(output2_rows)), "해당 raw 파일에 포함된 일봉 행 수"],
            ],
        )
        raw_section = raw_stats
        raw_section += '<h3>3-1. output1 실제값 정리</h3>'
        raw_section += '<p>output1은 조회 시점의 종목 스냅샷 값입니다. 가격, 호가, 거래량, 밸류에이션 지표를 담습니다.</p>'
        raw_section += output1_table
        raw_section += f'<h3>3-2. 최근 {RECENT_MONTHS}개월 Release snapshot 샘플(마지막 거래일)</h3>'
        raw_section += '<p>본 리포트에서 “최근 구간”으로 표시하는 가격/거래량/거래대금 값은 raw(output2)가 아니라 Release snapshot 기준입니다.</p>'
        raw_section += silver_recent_field_table
        if not recent_silver_df.empty:
            raw_section += f'<h3>3-3. 최근 {RECENT_MONTHS}개월 Release snapshot 샘플</h3>'
            raw_section += recent_rows_html
            raw_section += f'<h3>3-4. 최근 {RECENT_MONTHS}개월 Release snapshot 요약 통계</h3>'
            raw_section += recent_silver_stats_html
            raw_section += f'<div class="chart-grid"><figure><img src="{html.escape(raw_chart.name)}" alt="recent silver chart"></figure></div>'

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(symbol)} 종목 데이터 리포트</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 0; background: #f6f8fb; color: #1f2937; }}
    .wrap {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
    .hero {{ background: #111827; color: white; padding: 24px; border-radius: 12px; }}
    .hero h1 {{ margin: 0 0 8px; font-size: 28px; }}
    .meta {{ display: flex; gap: 16px; flex-wrap: wrap; color: #d1d5db; font-size: 14px; }}
    .section {{ background: white; margin-top: 20px; padding: 20px; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.05); }}
    h2 {{ margin: 0 0 16px; font-size: 22px; }}
    h3 {{ margin-top: 22px; }}
    .data-table {{ border-collapse: collapse; width: 100%; margin: 12px 0 18px; font-size: 14px; }}
    .data-table th, .data-table td {{ border: 1px solid #e5e7eb; padding: 8px 10px; text-align: left; vertical-align: top; }}
    .data-table th {{ background: #f3f4f6; }}
    .chart-grid {{ display: grid; grid-template-columns: 1fr; gap: 16px; margin: 16px 0; }}
    .chart-grid figure {{ margin: 0; background: #fff; }}
    .chart-grid img {{ max-width: 100%; border: 1px solid #e5e7eb; border-radius: 8px; }}
    .empty {{ color: #6b7280; font-style: italic; }}
    .note li {{ margin: 6px 0; }}
    @media (min-width: 960px) {{
      .chart-grid {{ grid-template-columns: 1fr 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <h1>{html.escape(symbol)} 종목 데이터 리포트</h1>
      <div class="meta">
        <div>작성 시각: {html.escape(report_ts)}</div>
        <div>종목명: {html.escape(str(universe.get('name', '-')))}</div>
        <div>시장: {html.escape(str(universe.get('market', '-')))}</div>
        <div>자산유형: {html.escape(str(universe.get('asset_type', '-')))}</div>
        <div>상장일: {html.escape(str(universe.get('listing_date', '-')))}</div>
        {release_hero_lines}
      </div>
    </div>
    <section class="section">
      <h2>1. 종목/수집 상태</h2>
      {summary_table}
    </section>
    <section class="section">
      <h2>2. 릴리즈 스냅샷 저장 현황</h2>
      {silver_section}
    </section>
    <section class="section">
      <h2>3. 최신 Raw + 최근 3개월 Release snapshot</h2>
      {raw_section}
    </section>
    <section class="section">
      <h2>4. 표시 방식 안내</h2>
      <ul class="note">
        <li>가격/거래량/거래대금처럼 시간 흐름을 보는 값은 차트로 표시했습니다.</li>
        <li>메타데이터, 상태값, 스냅샷 값은 표 형태로 정리했습니다.</li>
        <li>output1은 조회 시점 단일 스냅샷이라 표가 적합하고, output2는 시계열이므로 차트와 최근 행 샘플을 함께 보여줍니다.</li>
        <li>Release 스냅샷은 (symbol, date) 단위로 병합되며, 같은 날짜는 <strong>나중에 수집된 행(ingested_at이 큰 값)</strong>이 우선합니다.</li>
        <li>운영 스케줄: KST 16:30경 수집은 장중·마감 전 값일 수 있고, KST 20:15경(대체 20:00 종료 이후) 재수집으로 당일 봉을 갱신합니다.</li>
      </ul>
    </section>
  </div>
</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="실제 내부값 기반 종목 리포트 생성")
    parser.add_argument("--symbol", required=True, help="종목코드 (예: 005930)")
    parser.add_argument("--out-dir", default="reports", help="리포트 출력 디렉터리")
    parser.add_argument("--mode", choices=("release", "silver"), default="release", help="OHLCV 시계열 데이터 소스")
    parser.add_argument("--release-repo", default="chans-nim/Sauvignon", help="Release repo (owner/name or URL)")
    parser.add_argument(
        "--release-tag",
        default=None,
        help="Release tag (default: manifest vs GitHub 최신 중 태그 시각이 더 새 쪽)",
    )
    parser.add_argument(
        "--prefer-manifest",
        action="store_true",
        help="data_manifest.json latest_current를 우선 (GitHub에 더 새 릴리즈가 있어도 무시)",
    )
    parser.add_argument("--download-dir", default=DOWNLOAD_DIR.as_posix(), help="Release parquet 다운로드 디렉터리")
    args = parser.parse_args()

    symbol = args.symbol.strip()
    universe = get_universe_row(symbol)
    if universe is None:
        print(f"ERROR: symbol '{symbol}' not found in universe.", file=sys.stderr)
        sys.exit(2)

    out_dir = Path(args.out_dir) / symbol
    out_dir.mkdir(parents=True, exist_ok=True)

    state = get_collect_state(symbol)
    release_meta: dict | None = None
    if args.mode == "silver":
        silver_df = load_silver(symbol, str(universe["market"]))
    else:
        silver_df, release_meta = load_release_series(
            symbol,
            str(universe["market"]),
            repo=args.release_repo,
            tag=args.release_tag,
            download_dir=Path(args.download_dir),
            prefer_manifest=args.prefer_manifest,
        )
    raw_path, raw_data = load_latest_raw(symbol)
    report = build_report(
        symbol,
        universe,
        state,
        silver_df,
        raw_path,
        raw_data,
        out_dir,
        release_source_meta=release_meta,
    )

    report_path = out_dir / f"{symbol}_report.html"
    report_path.write_text(report, encoding="utf-8")

    print(f"Report written: {report_path}")
    print(f"Output directory: {out_dir}")
    if not silver_df.empty:
        max_d = silver_df["date"].max()
        max_date_str = max_d.date() if hasattr(max_d, "date") else str(max_d)[:10]
        source_name = "release snapshot" if args.mode == "release" else "local silver"
        print(f"{source_name} 최대일: {max_date_str}")
        if release_meta and release_meta.get("manifest_created_at"):
            print(f"manifest created_at(UTC): {release_meta['manifest_created_at']}")
        if release_meta and release_meta.get("symbol_last_day_max_ingested_at"):
            print(f"종목 최종일 max ingested_at: {release_meta['symbol_last_day_max_ingested_at']}")


if __name__ == "__main__":
    main()
