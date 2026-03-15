from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, TypeVar

import requests

T = TypeVar("T")

RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}
TOKEN_EXPIRED_HTTP_STATUS = {401, 403}


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 8.0
    backoff_multiplier: float = 2.0


@dataclass(frozen=True)
class RetryDecision:
    retryable: bool
    refresh_token: bool = False
    status_code: int | None = None
    reason: str = "non_retryable"


def compute_backoff_delay(attempt: int, policy: RetryPolicy) -> float:
    delay = policy.base_delay_seconds * (policy.backoff_multiplier ** max(0, attempt - 1))
    return min(policy.max_delay_seconds, delay)


def classify_retryable_exception(exc: Exception) -> RetryDecision:
    if isinstance(exc, requests.HTTPError):
        status = exc.response.status_code if exc.response is not None else None
        if status in TOKEN_EXPIRED_HTTP_STATUS:
            return RetryDecision(retryable=True, refresh_token=True, status_code=status, reason="token_expired")
        if status in RETRYABLE_HTTP_STATUS:
            return RetryDecision(retryable=True, status_code=status, reason=f"http_{status}")
        return RetryDecision(retryable=False, status_code=status, reason=f"http_{status}")
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return RetryDecision(retryable=True, reason="network_transient")
    if isinstance(exc, requests.RequestException):
        return RetryDecision(retryable=True, reason="request_exception")
    return RetryDecision(retryable=False, reason=exc.__class__.__name__)


def run_with_retry(
    fn: Callable[[], T],
    *,
    policy: RetryPolicy | None = None,
    on_retry: Callable[[int, RetryDecision, Exception], None] | None = None,
    on_token_refresh: Callable[[], None] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> T:
    policy = policy or RetryPolicy()
    last_exc: Exception | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            decision = classify_retryable_exception(exc)
            is_last_attempt = attempt >= policy.max_attempts
            if not decision.retryable or is_last_attempt:
                raise
            if decision.refresh_token and on_token_refresh is not None:
                on_token_refresh()
            if on_retry is not None:
                on_retry(attempt, decision, exc)
            sleep_fn(compute_backoff_delay(attempt, policy))
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("retry loop exited unexpectedly")
