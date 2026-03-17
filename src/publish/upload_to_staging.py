from __future__ import annotations
import argparse
import shutil
from pathlib import Path
from src.common.settings import settings
from src.common.logger import get_logger

log = get_logger(__name__)
DELTA_DIR = settings.project_root / "data" / "delta"
SNAPSHOT_DIR = settings.project_root / "data" / "snapshot"
STAGING_DIR = settings.staging_dir
STAGING_DIR.mkdir(parents=True, exist_ok=True)

def public_url(path: Path) -> str:
    base_url = settings.staging_base_url.rstrip("/")
    return f"{base_url}/{path.name}" if base_url else path.as_uri()


def source_dir_for_tag(tag: str) -> Path:
    if tag.startswith("data-delta-"):
        return DELTA_DIR
    if tag.startswith("data-snapshot-") or tag.startswith("data-full-"):
        return SNAPSHOT_DIR
    raise ValueError(f"unsupported tag format: {tag}")


def available_tags(src_dir: Path) -> list[str]:
    tags = {p.stem for p in src_dir.glob("*.parquet")}
    return sorted(tags)


def resolve_tag(tag: str) -> tuple[str, Path]:
    src_dir = source_dir_for_tag(tag)
    if (src_dir / f"{tag}.parquet").exists():
        return tag, src_dir

    candidates = [name for name in available_tags(src_dir) if name.startswith(tag)]
    if len(candidates) == 1:
        return candidates[0], src_dir
    if len(candidates) > 1:
        raise FileNotFoundError(f"ambiguous tag '{tag}' in {src_dir}; candidates: {', '.join(candidates)}")

    available = ", ".join(available_tags(src_dir)) or "(none)"
    raise FileNotFoundError(f"missing release asset for tag '{tag}' in {src_dir}; available tags: {available}")


def stage_release_assets(tag: str) -> list[Path]:
    resolved_tag, src_dir = resolve_tag(tag)
    copied: list[Path] = []

    for suffix in [".parquet", ".sha256", ".json"]:
        src = src_dir / f"{resolved_tag}{suffix}"
        if not src.exists():
            raise FileNotFoundError(f"missing release asset: {src}")
        dst = STAGING_DIR / src.name
        shutil.copy2(src, dst)
        copied.append(dst)
        log.info("copied %s", dst)

    return copied

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()

    copied = stage_release_assets(args.tag)

    print("\n[STAGING URLS]")
    for p in copied:
        print(public_url(p))

if __name__ == "__main__":
    main()
