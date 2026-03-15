from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.common.utils import sha256_file
from src.sync.consumer_sync import sync_latest_release
from src.sync.github_release_client import ReleaseAsset, ReleaseInfo


class FakeReleaseClient:
    def __init__(self, release: ReleaseInfo) -> None:
        self.release = release
        self.download_count = 0

    def get_latest_release(self) -> ReleaseInfo:
        return self.release

    def get_release_by_tag(self, tag: str) -> ReleaseInfo:
        if tag != self.release.tag:
            raise KeyError(tag)
        return self.release

    def download_asset(self, asset: ReleaseAsset, destination: Path) -> Path:
        self.download_count += 1
        source = Path(str(asset.download_url))
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        return destination


def make_release_files(base: Path, tag: str = "data-delta-20260313-0900") -> ReleaseInfo:
    release_dir = base / "release-source"
    release_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = release_dir / f"{tag}.parquet"
    sha_path = release_dir / f"{tag}.sha256"

    df = pd.DataFrame(
        [
            {
                "symbol": "005930",
                "market": "KOSPI",
                "date": pd.Timestamp("2026-03-10"),
                "open": 100,
                "high": 110,
                "low": 95,
                "close": 105,
                "volume": 1000,
                "value": 105000,
                "ingested_at": pd.Timestamp("2026-03-13 09:00:00"),
            },
            {
                "symbol": "005930",
                "market": "KOSPI",
                "date": pd.Timestamp("2026-03-11"),
                "open": 106,
                "high": 112,
                "low": 101,
                "close": 108,
                "volume": 1500,
                "value": 162000,
                "ingested_at": pd.Timestamp("2026-03-13 09:00:00"),
            },
        ]
    )
    df.to_parquet(parquet_path, index=False)
    sha_path.write_text(sha256_file(parquet_path) + "\n", encoding="utf-8")
    return ReleaseInfo(
        tag=tag,
        assets=(
            ReleaseAsset(name=parquet_path.name, download_url=str(parquet_path)),
            ReleaseAsset(name=sha_path.name, download_url=str(sha_path)),
        ),
    )


def test_sync_applies_unapplied_release_and_updates_state(tmp_path: Path) -> None:
    # 검증: 미적용 release는 다운로드/검증/upsert 후 applied state가 갱신된다.
    release = make_release_files(tmp_path)
    manifest_path = tmp_path / "data_manifest.json"
    state_path = tmp_path / "meta" / "applied_release_state.json"
    download_dir = tmp_path / "downloads"
    manifest_path.write_text(json.dumps({"latest_current": {"tag": release.tag}}, ensure_ascii=False, indent=2), encoding="utf-8")

    captured: dict[str, int] = {}

    def fake_upserter(df: pd.DataFrame) -> None:
        captured["rows"] = len(df)

    client = FakeReleaseClient(release)
    result = sync_latest_release(
        client,
        manifest_path=manifest_path,
        applied_state_path=state_path,
        download_dir=download_dir,
        parquet_upserter=fake_upserter,
    )

    assert result.applied is True
    assert result.tag == release.tag
    assert result.rows == 2
    assert captured["rows"] == 2
    assert client.download_count == 2

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["latest_applied_release"] == release.tag
    assert state["asset_name"] == f"{release.tag}.parquet"


def test_sync_skips_when_release_already_applied(tmp_path: Path) -> None:
    # 검증: 이미 적용된 release tag면 다운로드 없이 skip 된다.
    release = make_release_files(tmp_path)
    manifest_path = tmp_path / "data_manifest.json"
    state_path = tmp_path / "meta" / "applied_release_state.json"
    download_dir = tmp_path / "downloads"
    manifest_path.write_text(json.dumps({"latest_current": {"tag": release.tag}}, ensure_ascii=False, indent=2), encoding="utf-8")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"latest_applied_release": release.tag, "applied_at": "2026-03-13T09:00:00"}, ensure_ascii=False, indent=2), encoding="utf-8")

    client = FakeReleaseClient(release)
    result = sync_latest_release(
        client,
        manifest_path=manifest_path,
        applied_state_path=state_path,
        download_dir=download_dir,
        parquet_upserter=lambda df: None,
    )

    assert result.applied is False
    assert result.reason == "already_applied"
    assert client.download_count == 0


def test_sync_raises_on_sha256_mismatch(tmp_path: Path) -> None:
    # 검증: .sha256 값이 다르면 release 적용 전에 예외가 발생한다.
    release = make_release_files(tmp_path)
    manifest_path = tmp_path / "data_manifest.json"
    state_path = tmp_path / "meta" / "applied_release_state.json"
    download_dir = tmp_path / "downloads"
    bad_sha = tmp_path / "release-source" / f"{release.tag}.sha256"
    bad_sha.write_text("deadbeef\n", encoding="utf-8")
    manifest_path.write_text(json.dumps({"latest_current": {"tag": release.tag}}, ensure_ascii=False, indent=2), encoding="utf-8")

    client = FakeReleaseClient(release)
    with pytest.raises(ValueError, match="sha256 mismatch"):
        sync_latest_release(
            client,
            manifest_path=manifest_path,
            applied_state_path=state_path,
            download_dir=download_dir,
            parquet_upserter=lambda df: None,
        )
