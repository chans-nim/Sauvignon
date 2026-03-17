from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish import trigger_github_actions


def test_infer_release_type_from_tag() -> None:
    assert trigger_github_actions.infer_release_type("data-delta-20260316-0900") == "delta"
    assert trigger_github_actions.infer_release_type("data-snapshot-20260316-1800") == "snapshot"
    assert trigger_github_actions.infer_release_type("data-full-20260316") == "full"


def test_load_manifest_resolves_unique_snapshot_prefix(tmp_path: Path, monkeypatch) -> None:
    snapshot_dir = tmp_path / "data" / "snapshot"
    snapshot_dir.mkdir(parents=True)
    manifest = {
        "tag": "data-snapshot-20260316-1800",
        "file_name": "data-snapshot-20260316-1800.parquet",
        "sha256": "abc123",
        "row_count": 123,
        "min_date": "2016-01-04",
        "max_date": "2026-03-16",
    }
    (snapshot_dir / "data-snapshot-20260316-1800.json").write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setattr(trigger_github_actions, "SNAPSHOT_DIR", snapshot_dir)

    resolved_tag, loaded = trigger_github_actions.load_manifest("data-snapshot-20260316")

    assert resolved_tag == "data-snapshot-20260316-1800"
    assert loaded["file_name"] == manifest["file_name"]


def test_default_workflow_for_snapshot() -> None:
    assert trigger_github_actions.default_workflow_for("snapshot") == "publish-snapshot.yml"
    assert trigger_github_actions.default_workflow_for("full") == "publish-snapshot.yml"
    assert trigger_github_actions.default_workflow_for("delta") == "publish-delta.yml"
