from __future__ import annotations
from dataclasses import dataclass
from typing import Literal

from src.clients.kis_client import KISClient, KISConfig
from src.common.settings import settings


EnvName = Literal["prod", "vps"]


@dataclass(frozen=True)
class AuthProfile:
    """KIS 인증 프로파일 (실전 / 모의투자 구분)."""

    name: EnvName
    app_key: str
    app_secret: str
    base_url: str


def _build_profile(env: EnvName) -> AuthProfile:
    if env == "vps":
        # 모의투자용 키가 없으면 실전 키를 그대로 재사용할 수 있도록 한다.
        app_key = settings.kis_paper_app_key or settings.kis_app_key
        app_secret = settings.kis_paper_app_secret or settings.kis_app_secret
    else:
        app_key = settings.kis_app_key
        app_secret = settings.kis_app_secret
    return AuthProfile(
        name=env,
        app_key=app_key,
        app_secret=app_secret,
        base_url=settings.kis_base_url,
    )


_current_env: EnvName | None = None
_current_client: KISClient | None = None


def auth(svr: EnvName = "prod") -> KISClient:
    """
    KIS Open API 인증을 수행하고, 전역 KISClient 인스턴스를 초기화한다.

    예:
        from src.clients import kis_auth as ka
        client = ka.auth(svr="prod")  # 실전
        client = ka.auth(svr="vps")   # 모의투자
    """
    global _current_env, _current_client
    profile = _build_profile(svr)
    config = KISConfig(
        app_key=profile.app_key,
        app_secret=profile.app_secret,
        base_url=profile.base_url,
        token_url=settings.kis_token_url,
        daily_price_url=settings.kis_daily_price_url,
        daily_price_tr_id=settings.kis_daily_price_tr_id,
    )
    _current_client = KISClient(config=config)
    _current_env = svr
    return _current_client


def get_client() -> KISClient:
    """
    현재 활성화된 환경(settings.kis_env 또는 마지막 auth 호출)을 기준으로
    전역 KISClient 인스턴스를 반환한다.
    """
    global _current_client, _current_env
    if _current_client is not None:
        return _current_client
    # 아직 초기화되지 않았다면 설정값 기반으로 auth 수행
    env: EnvName = "prod" if settings.kis_env not in ("prod", "vps") else settings.kis_env  # type: ignore[assignment]
    return auth(env)


def current_env() -> EnvName:
    """현재 사용 중인 환경명을 반환한다 (prod / vps)."""
    if _current_env in ("prod", "vps"):
        return _current_env  # type: ignore[return-value]
    env: EnvName = "prod" if settings.kis_env not in ("prod", "vps") else settings.kis_env  # type: ignore[assignment]
    return env

