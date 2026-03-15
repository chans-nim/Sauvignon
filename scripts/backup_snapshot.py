from __future__ import annotations
from datetime import datetime
from pathlib import Path
import zipfile

from src.common.settings import settings
from src.common.logger import get_logger

log = get_logger(__name__)


def _add_dir_to_zip(zf: zipfile.ZipFile, root: Path, rel_prefix: str = "") -> None:
    for path in root.rglob("*"):
        if path.is_file():
            rel_path = Path(rel_prefix) / path.relative_to(root)
            zf.write(path, rel_path.as_posix())


def main() -> None:
    project_root = settings.project_root
    backups_dir = project_root / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = backups_dir / f"snapshot_{ts}.zip"

    data_dir = project_root / "data"
    meta_dir = project_root / "meta"
    manifest_path = project_root / "data_manifest.json"

    with zipfile.ZipFile(out_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        if data_dir.exists():
            log.info("include data dir: %s", data_dir)
            _add_dir_to_zip(zf, data_dir, "data")
        if meta_dir.exists():
            log.info("include meta dir: %s", meta_dir)
            _add_dir_to_zip(zf, meta_dir, "meta")
        if manifest_path.exists():
            log.info("include manifest: %s", manifest_path)
            zf.write(manifest_path, "data_manifest.json")

    log.info("snapshot created: %s", out_path)


if __name__ == "__main__":
    main()

