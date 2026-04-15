"""Abstract broker adapter: orders, balances, optional market stream."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any

from models import OrderRecord, OrderRequest


class BrokerInterface(ABC):
    """브로커 REST·시세 스트림을 추상화한다. 시장가/지정가는 ``OrderRequest.order_type``으로 표현한다."""

    @abstractmethod
    def connect(self) -> None:
        """세션·토큰·WS 핸드셰이크 등 연결 준비."""

    @abstractmethod
    def disconnect(self) -> None:
        """연결 종료 및 리소스 정리."""

    @abstractmethod
    def get_cash_balance(self) -> float:
        """주문 가능 현금(또는 예수금) 잔고."""

    @abstractmethod
    def get_positions(self) -> dict[str, Any]:
        """브로커 기준 보유 종목 스냅샷 (심볼 -> 수량 등)."""

    @abstractmethod
    def submit_order(self, order_request: OrderRequest) -> OrderRecord:
        """주문 제출. ACK/거절까지의 응답을 담은 ``OrderRecord``를 반환한다."""

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """미체결 주문 취소 시도. 성공 여부."""

    @abstractmethod
    def get_order(self, order_id: str) -> OrderRecord | None:
        """단일 주문 조회."""

    @abstractmethod
    def list_open_orders(self) -> list[OrderRecord]:
        """미체결·부분체결 등 열린 주문 목록."""

    @abstractmethod
    def poll_fills(self) -> list[OrderRecord]:
        """체결/부분체결 이벤트 폴링. 브로커가 푸시하는 구조면 내부 큐를 비운다."""

    @abstractmethod
    def stream_market_data(self, symbols: list[str]) -> Iterator[Any]:
        """
        실시간 시세 스트림(제너레이터).
        각 yield 값은 브로커별 raw 또는 ``MarketEvent``로 변환 가능한 객체여야 한다.
        """

    @abstractmethod
    def is_connected(self) -> bool:
        """연결 유지 여부."""
