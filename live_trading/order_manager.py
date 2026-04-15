"""Order submission, broker sync, and duplicate-order prevention."""

from __future__ import annotations

import logging
from typing import Callable

from broker_interface import BrokerInterface
from live_config import LiveTradingConfig
from models import OrderRecord, OrderRequest, OrderSide, OrderStatus

logger = logging.getLogger(__name__)

_TERMINAL = {
    OrderStatus.FILLED,
    OrderStatus.CANCELED,
    OrderStatus.REJECTED,
    OrderStatus.FAILED,
}


class OrderManager:
    """브로커에 주문을 넘기고 로컬 상태를 추적한다. 동일 종목 활성 주문이 있으면 추가 제출을 막는다."""

    def __init__(
        self,
        broker: BrokerInterface,
        config: LiveTradingConfig,
        logger_: logging.Logger | None = None,
        submit_retry: int = 2,
        retry_sleep_seconds: float = 0.2,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self._broker = broker
        self._config = config
        self._log = logger_ or logger
        self._submit_retry = max(0, submit_retry)
        self._retry_sleep = max(0.0, retry_sleep_seconds)
        self._sleep = sleeper or (lambda s: __import__("time").sleep(s))
        self.active_orders: dict[str, OrderRecord] = {}
        self.last_order_by_symbol: dict[str, str] = {}

    def can_submit(self, symbol: str, side: OrderSide) -> bool:
        """동일 심볼에 미종결 주문이 있으면 False."""
        for oid, rec in self.active_orders.items():
            if rec.symbol != symbol:
                continue
            if rec.status in _TERMINAL:
                continue
            self._log.debug("can_submit False: active %s %s %s", symbol, side.value, oid)
            return False
        return True

    def submit(self, order_request: OrderRequest) -> OrderRecord:
        """주문 제출 (재시도 포함). 브로커 응답으로 ``OrderRecord``를 갱신한다."""
        last_exc: Exception | None = None
        rec: OrderRecord | None = None
        for attempt in range(self._submit_retry + 1):
            try:
                rec = self._broker.submit_order(order_request)
                break
            except Exception as e:
                last_exc = e
                self._log.warning(
                    "submit_order failed attempt %s/%s: %s",
                    attempt + 1,
                    self._submit_retry + 1,
                    e,
                    exc_info=True,
                )
                if attempt < self._submit_retry:
                    self._sleep(self._retry_sleep)
        if rec is None:
            raise RuntimeError(f"submit_order failed after retries: {last_exc}") from last_exc

        self.active_orders[rec.order_id] = rec
        self.last_order_by_symbol[rec.symbol] = rec.order_id
        self._log.info(
            "Order submitted id=%s sym=%s side=%s status=%s",
            rec.order_id,
            rec.symbol,
            rec.side.value,
            rec.status.value,
        )
        return rec

    def refresh_open_orders(self) -> list[OrderRecord]:
        """브로커의 열린 주문 목록을 가져와 로컬과 비교(동기화 훅)."""
        try:
            remote = self._broker.list_open_orders()
        except Exception as e:
            self._log.error("list_open_orders failed: %s", e, exc_info=True)
            return []
        self._log.debug("refresh_open_orders count=%s", len(remote))
        return remote

    def process_fills(self) -> list[OrderRecord]:
        """``poll_fills`` 결과를 로컬 ``active_orders``에 반영하고 업데이트된 레코드를 반환한다."""
        try:
            updates = self._broker.poll_fills()
        except Exception as e:
            self._log.error("poll_fills failed: %s", e, exc_info=True)
            return []
        out: list[OrderRecord] = []
        for rec in updates:
            prev = self.active_orders.get(rec.order_id)
            self.active_orders[rec.order_id] = rec
            self._log.info(
                "Fill update id=%s sym=%s filled=%s/%s status=%s",
                rec.order_id,
                rec.symbol,
                rec.filled_qty,
                rec.qty,
                rec.status.value,
            )
            if prev is None or prev.status != rec.status:
                out.append(rec)
        return out

    def mark_order_closed(self, order_id: str) -> None:
        """체결/종료된 주문을 활성 목록에서 제거한다."""
        rec = self.active_orders.pop(order_id, None)
        if rec is None:
            return
        sym = rec.symbol
        if self.last_order_by_symbol.get(sym) == order_id:
            del self.last_order_by_symbol[sym]
        self._log.debug("mark_order_closed %s", order_id)
