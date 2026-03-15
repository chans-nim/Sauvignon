from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish.manifest_update import (
    build_release_entry,
    default_manifest,
    ensure_manifest_structure,
    update_manifest_payload,
)


def test_ensure_manifest_structure_adds_latest_current_and_release_history() -> None:
    # 검증: 구 manifest도 latest_current/release_tags 구조를 가진 새 형태로 보강된다.
    old_manifest = {
        "schema_version": 1,
        "latest_full": None,
        "latest_delta": {"tag": None, "min_date": None, "max_date": None, "assets": []},
        "history": {"delta_tags": []},
        "policy": {"rerun_window_trading_days": 10, "format": "parquet", "hash": "sha256"},
    }
    out = ensure_manifest_structure(old_manifest)
    assert out["schema_version"] == 1
    assert "latest_current" in out
    assert "release_tags" in out["history"]
    assert "release_type" in out["latest_delta"]
    assert "row_count" in out["latest_delta"]
    assert "created_at" in out["latest_delta"]


def test_update_manifest_payload_for_snapshot_updates_latest_current() -> None:
    # 검증: snapshot release는 latest_current와 루트 메타를 갱신한다.
    manifest = default_manifest()
    entry = build_release_entry(
        tag="data-snapshot-20260314-1130",
        release_type="snapshot",
        file_name="data-snapshot-20260314-1130.parquet",
        sha256="abc123",
        bytes_size=1234,
        created_at="2026-03-14T11:30:00Z",
        row_count=2500,
        min_date="2016-01-04",
        max_date="2026-03-14",
    )
    out = update_manifest_payload(manifest, entry)
    assert out["latest_current"]["tag"] == entry["tag"]
    assert out["latest_current"]["release_type"] == "snapshot"
    assert out["latest_full"] is None
    assert out["release_type"] == "snapshot"
    assert out["row_count"] == 2500
    assert out["history"]["release_tags"][0]["tag"] == entry["tag"]


def test_update_manifest_payload_for_full_updates_latest_full_and_current() -> None:
    # 검증: full release는 latest_full과 latest_current를 동시에 갱신한다.
    manifest = default_manifest()
    entry = build_release_entry(
        tag="data-full-20260314",
        release_type="full",
        file_name="data-full-20260314.parquet",
        sha256="def456",
        bytes_size=5678,
        created_at="2026-03-14T00:00:00Z",
        row_count=2500,
        min_date="2016-01-04",
        max_date="2026-03-14",
    )
    out = update_manifest_payload(manifest, entry)
    assert out["latest_full"]["tag"] == entry["tag"]
    assert out["latest_current"]["tag"] == entry["tag"]


def test_update_manifest_payload_for_delta_preserves_latest_current() -> None:
    # 검증: delta release는 latest_delta만 갱신하고 latest_current는 유지한다.
    manifest = default_manifest()
    current = build_release_entry(
        tag="data-snapshot-20260314-1130",
        release_type="snapshot",
        file_name="data-snapshot-20260314-1130.parquet",
        sha256="abc123",
        bytes_size=1234,
        created_at="2026-03-14T11:30:00Z",
        row_count=2500,
        min_date="2016-01-04",
        max_date="2026-03-14",
    )
    manifest = update_manifest_payload(manifest, current)

    delta = build_release_entry(
        tag="data-delta-20260314-1200",
        release_type="delta",
        file_name="data-delta-20260314-1200.parquet",
        sha256="delta123",
        bytes_size=777,
        created_at="2026-03-14T12:00:00Z",
        row_count=18,
        min_date="2026-03-13",
        max_date="2026-03-14",
    )
    out = update_manifest_payload(manifest, delta)
    assert out["latest_delta"]["tag"] == delta["tag"]
    assert out["latest_current"]["tag"] == current["tag"]
    assert out["history"]["delta_tags"][0] == delta["tag"]
    assert out["history"]["release_tags"][0]["tag"] == delta["tag"]
