"""
Google Drive 공유 링크(view 형태)로 올려둔 스냅샷 3종(parquet, json, sha256)을
로컬 data/snapshot 에 바로 다운로드합니다.
의존성: gdown (requirements.txt 포함). pip install -r requirements.txt

사용 예 (프로젝트 루트에서):
  python -m scripts.download_snapshot_from_drive --tag data-snapshot-20260317-1338 ^
    --parquet-url "https://drive.google.com/file/d/1kWG4xA5Sc-_0I41Ut53SW7StVAHHXo7u/view?usp=sharing" ^
    --json-url "https://drive.google.com/file/d/1fCeOAK4OSVELtOkkooW09xpF7kipUnTZ/view?usp=sharing" ^
    --sha256-url "https://drive.google.com/file/d/1FohRBvzgBn_116gvgJYok6opDwcDDyC2/view?usp=sharing"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __name__ == "__main__" and str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common.settings import settings

SNAPSHOT_DIR = settings.project_root / "data" / "snapshot"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download snapshot parquet/json/sha256 from Google Drive share links (view URL OK)"
    )
    default_tag = "data-snapshot-20260317-1338"
    default_parquet_url = "https://drive.google.com/file/d/1kWG4xA5Sc-_0I41Ut53SW7StVAHHXo7u/view?usp=sharing"
    default_json_url = "https://drive.google.com/file/d/1fCeOAK4OSVELtOkkooW09xpF7kipUnTZ/view?usp=sharing"
    default_sha256_url = "https://drive.google.com/file/d/1FohRBvzgBn_116gvgJYok6opDwcDDyC2/view?usp=sharing"
    parser.add_argument("--tag", default=default_tag, help=f"Snapshot tag (default: {default_tag})")
    parser.add_argument("--parquet-url", default=default_parquet_url, help="Drive share URL for .parquet")
    parser.add_argument("--json-url", default=default_json_url, help="Drive share URL for .json")
    parser.add_argument("--sha256-url", default=default_sha256_url, help="Drive share URL for .sha256")
    parser.add_argument("--out-dir", type=Path, default=None, help="Output directory (default: data/snapshot)")
    args = parser.parse_args()

    try:
        import gdown
    except ImportError:
        print("gdown이 필요합니다: pip install gdown")
        sys.exit(1)

    out_dir = args.out_dir or SNAPSHOT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = args.tag.rstrip("/")

    targets = [
        (args.parquet_url, out_dir / f"{tag}.parquet"),
        (args.json_url, out_dir / f"{tag}.json"),
        (args.sha256_url, out_dir / f"{tag}.sha256"),
    ]
    for url, path in targets:
        print(f"Downloading {path.name} ...")
        gdown.download(url, path.as_posix(), quiet=False, fuzzy=True)
        if not path.exists():
            print(f"Failed to download {path.name}")
            sys.exit(1)
    print(f"Done. Saved under {out_dir}")


if __name__ == "__main__":
    main()
