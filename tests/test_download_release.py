from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.sync.github_release_client import GitHubReleaseClient, ReleaseAsset, ReleaseInfo


class FakeBackend:
    def __init__(self, release: ReleaseInfo) -> None:
        self.release = release
        self.downloaded: list[tuple[str, Path]] = []

    def get_latest_release(self) -> ReleaseInfo:
        return self.release

    def get_release_by_tag(self, tag: str) -> ReleaseInfo:
        if tag != self.release.tag:
            raise KeyError(tag)
        return self.release

    def download_asset(self, asset: ReleaseAsset, destination: Path) -> Path:
        self.downloaded.append((asset.name, destination))
        source = Path(str(asset.download_url))
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        return destination


def test_client_delegates_latest_release_lookup(tmp_path: Path) -> None:
    # 검증: 최신 release 조회는 backend 결과를 그대로 반환한다.
    release = ReleaseInfo(tag="data-delta-20260316-0900", assets=())
    client = GitHubReleaseClient(backend=FakeBackend(release))
    assert client.get_latest_release().tag == release.tag


def test_client_returns_assets_by_tag(tmp_path: Path) -> None:
    # 검증: 특정 tag의 asset 목록을 list 형태로 받을 수 있다.
    asset = ReleaseAsset(name="sample.parquet", download_url="unused")
    release = ReleaseInfo(tag="data-delta-20260316-0900", assets=(asset,))
    client = GitHubReleaseClient(backend=FakeBackend(release))
    assets = client.get_assets_by_tag(release.tag)
    assert len(assets) == 1
    assert assets[0].name == "sample.parquet"


def test_client_download_asset_creates_parent_directory(tmp_path: Path) -> None:
    # 검증: 다운로드 대상 부모 디렉터리가 없어도 생성 후 저장한다.
    source = tmp_path / "source.parquet"
    source.write_bytes(b"parquet-bytes")
    asset = ReleaseAsset(name="sample.parquet", download_url=str(source))
    release = ReleaseInfo(tag="data-delta-20260316-0900", assets=(asset,))
    backend = FakeBackend(release)
    client = GitHubReleaseClient(backend=backend)

    dest = tmp_path / "nested" / "downloads" / "sample.parquet"
    out = client.download_asset(asset, dest)

    assert out == dest
    assert out.exists()
    assert out.read_bytes() == b"parquet-bytes"
    assert backend.downloaded[0][0] == "sample.parquet"
