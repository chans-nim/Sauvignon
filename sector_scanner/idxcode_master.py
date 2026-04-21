"""KRX 업종 마스터(idxcode.mst) 다운로드 및 파싱 (한국투자 FAQ 업종코드와 호환)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
import requests

IDX_ZIP_URL = "https://new.real.download.dws.co.kr/common/master/idxcode.mst.zip"


def default_cache_path(project_root: Path | None = None) -> Path:
    root = project_root or Path(__file__).resolve().parent.parent
    d = root / "data" / "raw" / "master"
    d.mkdir(parents=True, exist_ok=True)
    return d / "idxcode.mst"


def download_idxcode_mst(dest: Path | None = None, *, timeout: float = 60.0) -> Path:
    """idxcode.mst.zip을 내려받아 압축 해제 후 .mst 경로를 반환한다."""
    path = dest or default_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(IDX_ZIP_URL, timeout=timeout)
    r.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    zf.extractall(path.parent)
    zf.close()
    if not path.is_file():
        # zip root may differ
        found = list(path.parent.glob("**/idxcode.mst"))
        if not found:
            raise FileNotFoundError(f"idxcode.mst not found after extract under {path.parent}")
        return found[0]
    return path


def parse_idxcode_mst(mst_path: Path) -> pd.DataFrame:
    """
    IDX_CODE struct (한국투자 stocks_info/업종코드정보.h):
    idx_div[1], idx_code[4], idx_name[40] per fixed-width line.
    """
    rows: list[dict[str, Any]] = []
    with mst_path.open("r", encoding="cp949", errors="replace") as f:
        for line in f:
            r = line.rstrip("\n\r")
            # 실파일은 우측 공백이 잘린 짧은 행도 많다. code(5자)만 있으면 sector로 본다.
            if len(r) < 5:
                continue
            idx_div = r[0]
            idx_code = r[1:5]
            idx_name = r[5:].strip()
            if not idx_code.strip():
                continue
            rows.append(
                {
                    "idx_div": idx_div,
                    "sector_code_4": idx_code.strip(),
                    "sector_code_full": f"{idx_div}{idx_code}".strip(),
                    "sector_name": idx_name,
                }
            )
    return pd.DataFrame(rows)


def load_idxcode_dataframe(
    *,
    cache_path: Path | None = None,
    download_if_missing: bool = True,
) -> pd.DataFrame:
    p = cache_path or default_cache_path()
    if not p.is_file():
        if not download_if_missing:
            raise FileNotFoundError(f"idxcode.mst missing: {p}")
        p = download_idxcode_mst(p)
    return parse_idxcode_mst(p)
