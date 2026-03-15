from __future__ import annotations
import argparse, json, subprocess
from pathlib import Path
from src.common.settings import settings

DELTA_DIR = settings.project_root / "data" / "delta"

def load_manifest(tag: str) -> dict:
    path = DELTA_DIR / f"{tag}.json"
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--delta-url", required=True)
    parser.add_argument("--workflow-name", default="Publish Delta (Staging -> GitHub Release)")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()

    meta = load_manifest(args.tag)
    cmd = [
        "gh", "workflow", "run", args.workflow_name,
        "-f", f"tag={args.tag}",
        "-f", f"delta_url={args.delta_url}",
        "-f", f"sha256={meta['sha256']}",
        "-f", f"min_date={meta['min_date'] or ''}",
        "-f", f"max_date={meta['max_date'] or ''}",
        "-f", f"asset_name={meta['file_name']}",
    ]
    print("[COMMAND]")
    print(" ".join(cmd))
    if args.run:
        subprocess.run(cmd, check=True)
        print("[OK] workflow dispatched")
    else:
        print("[DRY-RUN] --run 옵션이 없어 실행하지 않았습니다.")

if __name__ == "__main__":
    main()
