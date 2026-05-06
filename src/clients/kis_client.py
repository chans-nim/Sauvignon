from __future__ import annotations
import time
import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional
import requests
from src.common.settings import settings
from src.collect.retry_policy import RetryPolicy, RetryDecision, run_with_retry


@dataclass
class KISConfig:
    app_key: str = settings.kis_app_key
    app_secret: str = settings.kis_app_secret
    base_url: str = settings.kis_base_url
    token_url: str = settings.kis_token_url
    daily_price_url: str = settings.kis_daily_price_url
    daily_price_tr_id: str = settings.kis_daily_price_tr_id


class KISClient:
    def __init__(self, config: Optional[KISConfig] = None, timeout: int = 20) -> None:
        self.config = config or KISConfig()
        self.timeout = timeout
        self.retry_policy = RetryPolicy()
        self._token: Optional[str] = None
        self._token_expire_ts: float = 0.0
        self._last_issue_ts: float = 0.0
        self._lock = threading.Lock()

    def issue_token(self) -> str:
        with self._lock:
            # 이미 유효한 토큰이 있으면 그대로 사용
            now = time.time()
            if self._token and now < self._token_expire_ts:
                return self._token
            # 토큰 발급 호출 빈도 제한 (대략 1분당 1회 수준으로 조절)
            if self._last_issue_ts:
                elapsed = now - self._last_issue_ts
                if elapsed < 60:
                    time.sleep(60 - elapsed)
            url = f"{self.config.base_url}{self.config.token_url}"
            payload = {
                "grant_type": "client_credentials",
                "appkey": self.config.app_key,
                "appsecret": self.config.app_secret,
            }
            resp = requests.post(url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            token = data.get("access_token")
            if not token:
                raise RuntimeError(f"Token issue failed: {data}")
            expires_in = int(data.get("expires_in", 86400))
            self._token = token
            self._token_expire_ts = time.time() + max(60, expires_in - 60)
            self._last_issue_ts = time.time()
            return token

    def get_token(self) -> str:
        # 토큰 만료 전이면 바로 재사용 (다중 스레드에서 동시 갱신 방지)
        with self._lock:
            now = time.time()
            if self._token and now < self._token_expire_ts:
                return self._token
        return self.issue_token()

    def refresh_token(self) -> str:
        with self._lock:
            self._token = None
            self._token_expire_ts = 0.0
        return self.issue_token()

    def _headers(self, tr_id: str) -> Dict[str, str]:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.get_token()}",
            "appkey": self.config.app_key,
            "appsecret": self.config.app_secret,
            "tr_id": tr_id,
        }

    def request_get(self, path: str, params: Dict[str, Any], tr_id: str) -> Dict[str, Any]:
        """
        KIS Open API 호출에 공통 retry policy 를 적용한다.
        - 401/403: 토큰 refresh 후 재시도
        - 429: backoff 후 재시도
        - 5xx / 네트워크 일시 오류: backoff 후 재시도
        - 응답 JSON 의 rt_cd 가 0/0000 이 아닐 경우 예외 발생
        """
        url = f"{self.config.base_url}{path}"

        def _request_once() -> Dict[str, Any]:
            resp = requests.get(url, headers=self._headers(tr_id), params=params, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            if str(data.get("rt_cd", "0")) not in {"0", "0000"}:
                raise RuntimeError(f"KIS API error: {data}")
            return data

        return run_with_retry(
            _request_once,
            policy=self.retry_policy,
            on_token_refresh=self.refresh_token,
        )

    def get_daily_ohlcv(self, symbol: str, start_date: str, end_date: str, market_div_code: str = "J") -> Dict[str, Any]:
        params = {
            "fid_cond_mrkt_div_code": market_div_code,
            "fid_input_iscd": symbol,
            "fid_input_date_1": start_date.replace("-", ""),
            "fid_input_date_2": end_date.replace("-", ""),
            "fid_period_div_code": "D",
            "fid_org_adj_prc": "1",
        }
        return self.request_get(self.config.daily_price_url, params, self.config.daily_price_tr_id)
