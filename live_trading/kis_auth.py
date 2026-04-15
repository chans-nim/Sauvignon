"""KIS OAuth / access-token lifecycle (구현은 환경·엔드포인트 설정 후 완성)."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

from kis_http_client import KISHTTPClient


@dataclass
class KISTokenBundle:
    access_token: str
    expires_at_epoch: float


class KISAuthManager:
    """
    앱키/시크릿으로 액세스 토큰 발급·갱신.

    Notes
    -----
    실제 path/body 필드명은 증권사 스펙에 맞춰 ``issue_token`` 내부를 완성한다.
    """

    def __init__(self, http: KISHTTPClient) -> None:
        self._http = http
        self._app_key = os.environ.get("KIS_APP_KEY", "")
        self._app_secret = os.environ.get("KIS_APP_SECRET", "")
        self._cached: KISTokenBundle | None = None

    def issue_token(self) -> KISTokenBundle:
        """TODO: KIS OAuth 발급 API 호출로 교체."""
        raise NotImplementedError(
            "KISAuthManager.issue_token: implement with env-driven endpoint + http.post_json"
        )

    def get_valid_token(self) -> str:
        """만료 임박 시 재발급."""
        now = time.time()
        if self._cached and now < self._cached.expires_at_epoch - 30:
            return self._cached.access_token
        bundle = self.issue_token()
        self._cached = bundle
        return bundle.access_token

    def build_auth_headers(self, tr_id: str) -> dict[str, str]:
        """TR_ID 등 공통 헤더 조립 (필드명은 실제 스펙에 맞게 조정)."""
        tok = self.get_valid_token()
        return {
            "authorization": f"Bearer {tok}",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "tr_id": tr_id,
        }
