"""Normalize a downloaded snapshot bundle for publication under a new release tag."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd


def _replace_strings(value, old_tag: str, new_tag: str):
    if isinstance(value, str):
        return value.replace(old_tag, new_tag)
    if isinstance(value, list):
        return [_replace_strings(x, old_tag, new_tag) for x in value]
    if isinstance(value, dict):
        return {k: _replace_strings(v, old_tag, new_tag) for k, v in value.items()}
    return value


def prepare_bundle(root: Path, *, old_tag: str, new_tag: str, main_name: str) -> Path:
    root = Path(root)
    main_path = root / main_name
    if not main_path.is_file():
        raise FileNotFoundError(main_path)

    for path in list(root.iterdir()):
        if not path.name.endswith((".ticker-state.parquet", ".output1-latest.parquet")):
            continue
        frame = pd.read_parquet(path)
        if "snapshot_tag" in frame.columns:
            frame["snapshot_tag"] = new_tag
        frame.to_parquet(path, index=False)

    for path in list(root.iterdir()):
        if path.name == main_name:
            dest = root / f"{new_tag}.parquet"
        elif path.name.startswith(old_tag + "."):
            dest = root / (new_tag + path.name[len(old_tag) :])
        else:
            continue
        if dest != path:
            path.replace(dest)

    new_main = root / f"{new_tag}.parquet"
    if not new_main.is_file():
        raise FileNotFoundError(new_main)

    for path in root.glob("*.json"):
        payload = _replace_strings(json.loads(path.read_text(encoding="utf-8")), old_tag, new_tag)
        for asset in payload.get("assets") or []:
            asset_path = root / str(asset.get("name") or "")
            if asset_path.is_file():
                asset["sha256"] = hashlib.sha256(asset_path.read_bytes()).hexdigest()
                asset["bytes"] = asset_path.stat().st_size
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return new_main


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--old-tag", required=True)
    parser.add_argument("--new-tag", required=True)
    parser.add_argument("--main-name", required=True)
    args = parser.parse_args()
    print(
        prepare_bundle(
            args.dir,
            old_tag=args.old_tag,
            new_tag=args.new_tag,
            main_name=args.main_name,
        ).name
    )


if __name__ == "__main__":
    main()
