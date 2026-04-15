"""
KIS 브로커: HTTP·인증·REST주문·WS시세 helper를 조합한다.
실제 TR_ID/URL/필드는 ``kis_*`` 모듈에서 환경·상수로 채운다.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from typing import Any

from broker_interface import BrokerInterface
from kis_auth import KISAuthManager
from kis_http_client import KISHTTPClient
from kis_rest_orders import KISRestOrderClient
from kis_ws_quotes import KISWebSocketQuoteClient
from models import OrderRecord, OrderRequest

logger = logging.getLogger(__name__)


class KISBroker(BrokerInterface):
    """
    ``KISHTTPClient`` + ``KISAuthManager`` + ``KISRestOrderClient`` + ``KISWebSocketQuoteClient`` 조합.

    네트워크 호출은 ``KISHTTPClient(..., request_fn=...)`` 주입으로 단위테스트에서 대체한다.
    """

    def __init__(
        self,
        rest_base_url: str | None = None,
        ws_url: str | None = None,
        approval_key: str | None = None,
        http_request_fn: Any | None = None,
    ) -> None:
        base = rest_base_url or os.environ.get("KIS_REST_BASE", "https://invalid.local")
        self._http = KISHTTPClient(base, request_fn=http_request_fn)
        self._auth = KISAuthManager(self._http)
        self._rest_orders = KISRestOrderClient(self._auth, self._http)
        self._ws = KISWebSocketQuoteClient(
            ws_url or os.environ.get("KIS_WS_URL", "wss://invalid.local"),
            approval_key or os.environ.get("KIS_WS_APPROVAL_KEY", ""),
        )
        self._connected = False

    @property
    def rest_orders(self) -> KISRestOrderClient:
        return self._rest_orders

    @property
    def quotes_ws(self) -> KISWebSocketQuoteClient:
        return self._ws

    def connect(self) -> None:
        logger.warning(
            "KISBroker.connect: skeleton — implement token issue (KISAuthManager.issue_token) before trading."
        )
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def get_cash_balance(self) -> float:
        raise NotImplementedError("KISBroker.get_cash_balance: use REST inquiry via KISHTTPClient when wired")

    def get_positions(self) -> dict[str, Any]:
        raise NotImplementedError("KISBroker.get_positions: TODO REST balance/position inquiry")

    def submit_order(self, order_request: OrderRequest) -> OrderRecord:
        raise NotImplementedError(
            "KISBroker.submit_order: map OrderRequest → KISRestOrderClient.place_order body"
        )

    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError("KISBroker.cancel_order: delegate to KISRestOrderClient.cancel_order")

    def get_order(self, order_id: str) -> OrderRecord | None:
        raise NotImplementedError("KISBroker.get_order: TODO polling endpoint")

    def list_open_orders(self) -> list[OrderRecord]:
        raise NotImplementedError("KISBroker.list_open_orders: KISRestOrderClient.fetch_open_orders")

    def poll_fills(self) -> list[OrderRecord]:
        raise NotImplementedError("KISBroker.poll_fills: merge WS fills or REST order status")

    def stream_market_data(self, symbols: list[str]) -> Iterator[Any]:
        """TODO: ``KISWebSocketQuoteClient.iter_ticks`` → ``MarketEvent`` 변환."""
        raise NotImplementedError(
            f"KISBroker.stream_market_data: implement WS subscribe; symbols={symbols!r}"
        )
