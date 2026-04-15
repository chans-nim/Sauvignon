"""KIS WebSocket 시세 스트림 (구독·핑·재연결은 TODO)."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

logger = logging.getLogger(__name__)


class KISWebSocketQuoteClient:
    """
    실시간 호가/체결 틱을 ``iter_ticks``로 노출할 예정.

    실제 구현 시 ``websocket-client`` 또는 ``websockets`` 등을 래핑하고,
    브로커 레이어에서는 ``MarketEvent``로 변환한다.
    """

    def __init__(self, ws_url: str, approval_key: str) -> None:
        self._ws_url = ws_url.rstrip("/")
        self._approval_key = approval_key

    def iter_ticks(self, symbols: list[str]) -> Iterator[dict[str, Any]]:
        """TODO: 구독 메시지 전송 후 수신 루프."""
        raise NotImplementedError(
            f"KISWebSocketQuoteClient.iter_ticks not implemented; symbols={symbols!r}"
        )
