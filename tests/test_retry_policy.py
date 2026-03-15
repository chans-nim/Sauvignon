from __future__ import annotations

import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.collect.retry_policy import RetryPolicy, classify_retryable_exception, compute_backoff_delay, run_with_retry


def make_http_error(status_code: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status_code
    response._content = b""
    return requests.HTTPError(response=response)


def test_classify_429_as_retryable() -> None:
    # 검증: 429는 backoff 재시도 대상이다.
    decision = classify_retryable_exception(make_http_error(429))
    assert decision.retryable is True
    assert decision.refresh_token is False


def test_classify_5xx_as_retryable() -> None:
    # 검증: 5xx는 재시도 대상이다.
    decision = classify_retryable_exception(make_http_error(503))
    assert decision.retryable is True
    assert decision.status_code == 503


def test_classify_401_as_token_refresh() -> None:
    # 검증: 401/403은 token refresh 후 재시도 대상으로 분류된다.
    decision = classify_retryable_exception(make_http_error(401))
    assert decision.retryable is True
    assert decision.refresh_token is True


def test_classify_network_error_as_retryable() -> None:
    # 검증: 일시적 네트워크 오류는 재시도 대상으로 본다.
    decision = classify_retryable_exception(requests.ConnectionError("temporary"))
    assert decision.retryable is True


def test_run_with_retry_retries_then_succeeds() -> None:
    # 검증: retry loop는 5xx와 token refresh 케이스를 거쳐 성공 응답을 반환한다.
    calls = {"n": 0}
    refresh_calls = {"n": 0}
    sleep_calls: list[float] = []

    def fn():
        calls["n"] += 1
        if calls["n"] == 1:
            raise make_http_error(503)
        if calls["n"] == 2:
            raise make_http_error(401)
        return "ok"

    result = run_with_retry(
        fn,
        policy=RetryPolicy(max_attempts=4, base_delay_seconds=0.1, max_delay_seconds=1.0),
        on_token_refresh=lambda: refresh_calls.__setitem__("n", refresh_calls["n"] + 1),
        sleep_fn=lambda s: sleep_calls.append(s),
    )
    assert result == "ok"
    assert calls["n"] == 3
    assert refresh_calls["n"] == 1
    assert sleep_calls == [0.1, 0.2]


def test_compute_backoff_delay_caps_at_max() -> None:
    # 검증: exponential backoff는 최대 지연 시간에서 cap 된다.
    policy = RetryPolicy(base_delay_seconds=1.0, max_delay_seconds=3.0, backoff_multiplier=2.0)
    assert compute_backoff_delay(1, policy) == 1.0
    assert compute_backoff_delay(2, policy) == 2.0
    assert compute_backoff_delay(3, policy) == 3.0
    assert compute_backoff_delay(4, policy) == 3.0
