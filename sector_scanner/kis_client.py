"""KIS REST/WebSocket 클라이언트 스켈레톤."""

from __future__ import annotations

import random
import time
from collections.abc import Iterator
from typing import Any

import requests

from .kis_endpoints import KIS_REST_BASE_URL, KIS_WS_URL


def sleep_backoff(attempt: int, base: float = 0.5, cap: float = 8.0) -> None:
    """Rate limit / 일시 오류 대비 지수 백오프 + 소량 지터."""
    t = min(cap, base * (2**attempt)) + random.uniform(0, 0.25)
    time.sleep(t)


class KISClient:
    """
    KIS Open API 래퍼.

    실연동 전까지 메서드 본문은 TODO/NotImplementedError로 두고,
    인증·엔드포인트는 ``kis_endpoints`` 및 환경설정으로 분리한다.
    """

    def __init__(
        self,
        app_key: str | None = None,
        app_secret: str | None = None,
        account_no: str | None = None,
        *,
        base_url: str = KIS_REST_BASE_URL,
        ws_url: str = KIS_WS_URL,
        timeout_sec: float = 15.0,
    ) -> None:
        self._app_key = app_key
        self._app_secret = app_secret
        self._account_no = account_no
        self._base_url = base_url.rstrip("/")
        self._ws_url = ws_url
        self._timeout_sec = timeout_sec
        self._token: str | None = None
        self._session = requests.Session()

    def authenticate(self) -> None:
        """OAuth 토큰 발급 (TODO: 실제 KIS OAuth 플로우 구현)."""
        raise NotImplementedError(
            "KISClient.authenticate: implement OAuth token flow; see kis_endpoints and official docs."
        )

    def is_authenticated(self) -> bool:
        return self._token is not None

    def _request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self._base_url}{path}"
        try:
            r = self._session.request(method, url, timeout=self._timeout_sec, **kwargs)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            raise RuntimeError(f"KIS REST request failed: {method} {url} — {e}") from e

    def fetch_sector_current_index(self) -> list[dict[str, Any]]:
        """업종/섹터 현재 지수 스냅샷."""
        raise NotImplementedError("KISClient.fetch_sector_current_index: map TR id and parse response.")

    def fetch_sector_intraday_index(self) -> list[dict[str, Any]]:
        """업종 당일 분봉/시계열 지수."""
        raise NotImplementedError("KISClient.fetch_sector_intraday_index: map TR id and parse response.")

    def fetch_ranking_volume(self) -> list[dict[str, Any]]:
        """순위분석 — 거래량."""
        raise NotImplementedError("KISClient.fetch_ranking_volume: map TR id and parse response.")

    def fetch_ranking_return(self) -> list[dict[str, Any]]:
        """순위분석 — 등락률."""
        raise NotImplementedError("KISClient.fetch_ranking_return: map TR id and parse response.")

    def fetch_ranking_trade_strength(self) -> list[dict[str, Any]]:
        """순위분석 — 체결강도 등."""
        raise NotImplementedError("KISClient.fetch_ranking_trade_strength: map TR id and parse response.")

    def fetch_ranking_near_high(self) -> list[dict[str, Any]]:
        """순위분석 — 고가 근접."""
        raise NotImplementedError("KISClient.fetch_ranking_near_high: map TR id and parse response.")

    def fetch_ranking_block_trades(self) -> list[dict[str, Any]]:
        """순위분석 — 대량/블록 성격 지표 (가용 시)."""
        raise NotImplementedError("KISClient.fetch_ranking_block_trades: map TR id and parse response.")

    def fetch_stock_price(self, symbol: str) -> dict[str, Any]:
        """종목 현재가 단건."""
        raise NotImplementedError(f"KISClient.fetch_stock_price({symbol!r}): map TR id and parse response.")

    def fetch_stock_intraday_bars(self, symbol: str) -> list[dict[str, Any]]:
        """종목 당일 분봉."""
        raise NotImplementedError(f"KISClient.fetch_stock_intraday_bars({symbol!r}): map TR id and parse response.")

    def fetch_program_flow(self) -> list[dict[str, Any]]:
        """프로그램 매매 흐름 (섹터/종목 단위 가용 범위에 맞게 파싱)."""
        raise NotImplementedError("KISClient.fetch_program_flow: map TR id and parse response.")

    def fetch_foreign_institution_flow(self) -> list[dict[str, Any]]:
        """외국인/기관 순매수 등 수급."""
        raise NotImplementedError("KISClient.fetch_foreign_institution_flow: map TR id and parse response.")

    def fetch_universe_rows(self) -> list[dict[str, Any]]:
        """전 종목(또는 압축 유니버스) 메타 + 유동성 지표."""
        raise NotImplementedError("KISClient.fetch_universe_rows: implement master/liquidity query.")

    def fetch_price_history_frame(self, symbols: list[str], *, days: int = 60) -> Any:
        """종목별 종가 히스토리 (열=심볼, 인덱스=일자). pandas.DataFrame 권장."""
        raise NotImplementedError(f"KISClient.fetch_price_history_frame({symbols[:3]}..., days={days}): implement history TR.")

    def stream_ticks(self, symbols: list[str]) -> Iterator[dict[str, Any]]:
        """
        실시간 체결 스트림 (제너레이터).

        Yields
        ------
        dict
            최소 키: symbol, price, volume, ts (ISO 문자열 권장)
        """
        raise NotImplementedError(
            f"KISClient.stream_ticks({symbols!r}): connect WebSocket at {self._ws_url} and yield normalized events."
        )
        yield  # pragma: no cover — unreachable; satisfies Iterator typing
