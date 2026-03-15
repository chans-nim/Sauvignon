from __future__ import annotations
import argparse, shutil
from pathlib import Path
from src.common.settings import settings
from src.common.logger import get_logger

log = get_logger(__name__)
DELTA_DIR = settings.project_root / "data" / "delta"
STAGING_DIR = settings.staging_dir
STAGING_DIR.mkdir(parents=True, exist_ok=True)

def public_url(path: Path) -> str:
    base_url = settings.staging_base_url.rstrip("/")
    return f"{base_url}/{path.name}" if base_url else path.as_uri()

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()

    copied = []
    for suffix in [".parquet", ".sha256", ".json"]:
        src = DELTA_DIR / f"{args.tag}{suffix}"
        if not src.exists():
            raise FileNotFoundError(src)
        dst = STAGING_DIR / src.name
        shutil.copy2(src, dst)
        copied.append(dst)
        log.info("copied %s", dst)

    print("\n[STAGING URLS]")
    for p in copied:
        print(public_url(p))

if __name__ == "__main__":
    main()
