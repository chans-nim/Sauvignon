"""
KIS Open API 경로·헤더 상수 (실연동 시 설정/문서와 맞출 것).

실제 호출 전 토큰·TR ID·URL은 환경변수 또는 별도 설정으로 주입하는 것을 권장한다.
"""

from __future__ import annotations

# TODO: 실계정 연동 시 공식 문서의 REST base URL 및 WebSocket URL로 교체
KIS_REST_BASE_URL: str = "https://openapi.koreainvestment.com:9443"
KIS_WS_URL: str = "wss://openapi.koreainvestment.com:9443/websocket"

# TODO: 업종 지수, 순위분석, 시세, 수급 등 TR ID
TR_SECTOR_CURRENT: str = "TODO_SECTOR_CURRENT"
TR_SECTOR_INTRADAY: str = "TODO_SECTOR_INTRADAY"
TR_RANK_VOLUME: str = "TODO_RANK_VOLUME"
TR_RANK_RETURN: str = "TODO_RANK_RETURN"
TR_RANK_TRADE_STR: str = "TODO_RANK_TRADE_STR"
TR_RANK_NEAR_HIGH: str = "TODO_RANK_NEAR_HIGH"
TR_RANK_BLOCK: str = "TODO_RANK_BLOCK"
TR_STOCK_PRICE: str = "TODO_STOCK_PRICE"
TR_STOCK_MINUTE: str = "TODO_STOCK_MINUTE"
TR_PROGRAM_FLOW: str = "TODO_PROGRAM_FLOW"
TR_FOREIGN_INST_FLOW: str = "TODO_FOREIGN_INST_FLOW"
