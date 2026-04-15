"""KIS REST 주문·조회 클라이언트 (엔드포인트·바디는 TODO)."""

from __future__ import annotations

import logging
from typing import Any

from kis_auth import KISAuthManager
from kis_http_client import KISHTTPClient, JSONDict

logger = logging.getLogger(__name__)


class KISRestOrderClient:
    """현금 주문/정정/취소/미체결 조회 등을 담당할 자리."""

    def __init__(self, auth: KISAuthManager, http: KISHTTPClient) -> None:
        self._auth = auth
        self._http = http

    def place_order(self, body: JSONDict) -> JSONDict:
        """TODO: 실제 TR_ID·URL 상수 분리 후 구현."""
        raise NotImplementedError("KISRestOrderClient.place_order: wire http.post_json with KIS spec")

    def cancel_order(self, body: JSONDict) -> JSONDict:
        raise NotImplementedError("KISRestOrderClient.cancel_order")

    def fetch_open_orders(self) -> list[dict[str, Any]]:
        raise NotImplementedError("KISRestOrderClient.fetch_open_orders")
