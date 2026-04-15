"""Injectable HTTP facade for KIS REST (endpoint paths stay configurable)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

logger = logging.getLogger(__name__)

JSONDict = dict[str, Any]
RequestFn = Callable[..., Any]


class KISHTTPClient:
    """
    실제 호출은 ``request_fn``으로 주입한다 (단위테스트에서 ``unittest.mock.Mock`` 등).

    Parameters
    ----------
    base_url:
        예: 모의투자/실전 도메인은 설정·환경변수에서 로드 (하드코딩 금지 원칙).
    request_fn:
        ``(method, url, *, headers, json, timeout) -> response_like`` 시그니처 권장.
        ``response_like.json()`` 이 있으면 ``request_json``에서 사용한다.
    """

    def __init__(
        self,
        base_url: str,
        request_fn: RequestFn | None = None,
        default_timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._request_fn = request_fn
        self._timeout = default_timeout

    def _call(self, method: str, path: str, *, headers: Mapping[str, str], json_body: JSONDict | None) -> Any:
        if self._request_fn is None:
            raise RuntimeError(
                "KISHTTPClient: request_fn not injected. "
                "Provide a callable for network I/O (or use tests/mocks)."
            )
        url = f"{self.base_url}{path}"
        logger.debug("KIS HTTP %s %s", method, url)
        return self._request_fn(
            method,
            url,
            headers=dict(headers),
            json=json_body,
            timeout=self._timeout,
        )

    def post_json(self, path: str, headers: Mapping[str, str], body: JSONDict) -> JSONDict:
        """POST 후 JSON dict 반환. 응답 객체는 ``.json()`` 메서드를 제공해야 한다."""
        resp = self._call("POST", path, headers=headers, json_body=body)
        if hasattr(resp, "json"):
            return resp.json()
        raise TypeError("response must provide .json() for post_json")

    def get_json(self, path: str, headers: Mapping[str, str], params: Mapping[str, Any] | None = None) -> JSONDict:
        resp = self._call("GET", path, headers=headers, json_body=None)
        if hasattr(resp, "json"):
            return resp.json()
        raise TypeError("response must provide .json() for get_json")
