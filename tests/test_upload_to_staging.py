from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publish import upload_to_staging


def test_stage_release_assets_copies_snapshot_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 검증: snapshot/full tag는 data/snapshot 위치의 산출물을 staging으로 복사한다.
    snapshot_dir = tmp_path / "data" / "snapshot"
    staging_dir = tmp_path / "staging"
    snapshot_dir.mkdir(parents=True)
    staging_dir.mkdir(parents=True)

    tag = "data-snapshot-20260316-1800"
    for suffix, content in {
        ".parquet": b"parquet-bytes",
        ".sha256": b"abc123\n",
        ".json": b'{"tag":"data-snapshot-20260316-1800"}',
    }.items():
        (snapshot_dir / f"{tag}{suffix}").write_bytes(content)

    monkeypatch.setattr(upload_to_staging, "SNAPSHOT_DIR", snapshot_dir)
    monkeypatch.setattr(upload_to_staging, "STAGING_DIR", staging_dir)

    copied = upload_to_staging.stage_release_assets(tag)

    assert [p.name for p in copied] == [f"{tag}.parquet", f"{tag}.sha256", f"{tag}.json"]
    assert (staging_dir / f"{tag}.parquet").read_bytes() == b"parquet-bytes"


def test_stage_release_assets_resolves_unique_prefix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 검증: 날짜 prefix만 넘겨도 후보가 하나면 실제 tag로 해석해 복사한다.
    snapshot_dir = tmp_path / "data" / "snapshot"
    staging_dir = tmp_path / "staging"
    snapshot_dir.mkdir(parents=True)
    staging_dir.mkdir(parents=True)

    full_tag = "data-snapshot-20260316-1800"
    for suffix in [".parquet", ".sha256", ".json"]:
        (snapshot_dir / f"{full_tag}{suffix}").write_text("x", encoding="utf-8")

    monkeypatch.setattr(upload_to_staging, "SNAPSHOT_DIR", snapshot_dir)
    monkeypatch.setattr(upload_to_staging, "STAGING_DIR", staging_dir)

    copied = upload_to_staging.stage_release_assets("data-snapshot-20260316")

    assert [p.name for p in copied] == [f"{full_tag}.parquet", f"{full_tag}.sha256", f"{full_tag}.json"]


def test_stage_release_assets_shows_available_tags_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 검증: 없는 tag는 해당 디렉터리의 available tags와 함께 안내한다.
    snapshot_dir = tmp_path / "data" / "snapshot"
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / "data-full-20260316.parquet").write_text("x", encoding="utf-8")

    monkeypatch.setattr(upload_to_staging, "SNAPSHOT_DIR", snapshot_dir)

    with pytest.raises(FileNotFoundError, match="available tags: data-full-20260316"):
        upload_to_staging.stage_release_assets("data-snapshot-20260316")


def test_source_dir_for_tag_rejects_unknown_tag() -> None:
    # 검증: 지원하지 않는 tag 형식은 조용히 delta로 처리하지 않고 즉시 실패한다.
    with pytest.raises(ValueError):
        upload_to_staging.source_dir_for_tag("release-20260316")
