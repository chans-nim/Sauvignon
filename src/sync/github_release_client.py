from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Sequence


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    download_url: str | None = None
    size: int | None = None
    sha256: str | None = None


@dataclass(frozen=True)
class ReleaseInfo:
    tag: str
    assets: tuple[ReleaseAsset, ...] = field(default_factory=tuple)
    published_at: str | None = None
    release_type: str | None = None


class ReleaseBackend(Protocol):
    def get_latest_release(self) -> ReleaseInfo: ...

    def get_release_by_tag(self, tag: str) -> ReleaseInfo: ...

    def download_asset(self, asset: ReleaseAsset, destination: Path) -> Path: ...


class UnsupportedReleaseBackend:
    """
    실제 GitHub API/gh 구현 전까지 사용하는 기본 백엔드.
    나중에 이 클래스를 실제 구현체로 교체하면 된다.
    """

    def get_latest_release(self) -> ReleaseInfo:
        raise NotImplementedError("Release backend is not configured.")

    def get_release_by_tag(self, tag: str) -> ReleaseInfo:
        raise NotImplementedError("Release backend is not configured.")

    def download_asset(self, asset: ReleaseAsset, destination: Path) -> Path:
        raise NotImplementedError("Release backend is not configured.")


class GitHubReleaseClient:
    def __init__(self, backend: ReleaseBackend | None = None) -> None:
        self.backend = backend or UnsupportedReleaseBackend()

    def get_latest_release(self) -> ReleaseInfo:
        return self.backend.get_latest_release()

    def get_release_by_tag(self, tag: str) -> ReleaseInfo:
        return self.backend.get_release_by_tag(tag)

    def get_assets_by_tag(self, tag: str) -> list[ReleaseAsset]:
        return list(self.get_release_by_tag(tag).assets)

    def download_asset(self, asset: ReleaseAsset, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        return self.backend.download_asset(asset, destination)

