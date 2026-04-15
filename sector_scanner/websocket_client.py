"""실시간 체결 스트림 어댑터."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any, Protocol


class _TickClient(Protocol):
    def stream_ticks(self, symbols: list[str]) -> Iterator[dict[str, Any]]: ...


logger = logging.getLogger(__name__)


class WebSocketClientAdapter:
    """client.stream_ticks를 표준 이벤트 dict로 감싼다."""

    def __init__(self, client: _TickClient, logger_: logging.Logger | None = None) -> None:
        self._client = client
        self._log = logger_ or logger

    def stream(self, symbols: list[str]) -> Iterator[dict[str, Any]]:
        """
        Yields
        ------
        dict
            symbol, price, volume, ts, source
        """
        attempt = 0
        while attempt < 3:
            try:
                for ev in self._client.stream_ticks(symbols):
                    if not isinstance(ev, dict):
                        continue
                    sym = str(ev.get("symbol", "")).strip()
                    yield {
                        "symbol": sym,
                        "price": float(ev.get("price", 0.0)),
                        "volume": float(ev.get("volume", 0.0)),
                        "ts": str(ev.get("ts", "")),
                        "source": "stream_ticks",
                    }
                return
            except Exception as e:
                self._log.warning("WebSocketClientAdapter.stream attempt %d failed: %s", attempt + 1, e)
                attempt += 1
                time.sleep(0.2 * (2**attempt))
        self._log.error("WebSocketClientAdapter.stream exhausted retries for symbols=%s", symbols)
