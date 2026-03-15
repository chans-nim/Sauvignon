from __future__ import annotations
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.request import urlretrieve
from src.common.settings import settings
from src.common.logger import get_logger
from src.common.utils import sha256_file

log = get_logger(__name__)

@dataclass
class MasterDownloadTarget:
    market: str
    url: str
    zip_name: str
    mst_name: str

RAW_MASTER_DIR = settings.project_root / "data" / "raw" / "master"
RAW_MASTER_DIR.mkdir(parents=True, exist_ok=True)

TARGETS = [
    MasterDownloadTarget("KOSPI", "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip", "kospi_code.mst.zip", "kospi_code.mst"),
    MasterDownloadTarget("KOSDAQ", "https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip", "kosdaq_code.mst.zip", "kosdaq_code.mst"),
]

def main() -> None:
    for target in TARGETS:
        zip_path = RAW_MASTER_DIR / target.zip_name
        log.info("download %s: %s", target.market, target.url)
        urlretrieve(target.url, zip_path.as_posix())
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(RAW_MASTER_DIR)
        mst_path = RAW_MASTER_DIR / target.mst_name
        if not mst_path.exists():
            raise FileNotFoundError(mst_path)
        log.info("ready %s sha256=%s", mst_path.name, sha256_file(mst_path))

if __name__ == "__main__":
    main()
