from __future__ import annotations
from datetime import datetime
from src.clients.kis_auth import get_client
from src.collect.base_collect import fetch_ohlcv_chunked, validate_ohlcv
from src.storage import meta_store, parquet_store
from src.common.logger import get_logger
from src.common.settings import settings

log = get_logger(__name__)

# 스킵된 청크 중 이 비율 미만이면 "이상치"로 로그 (전체 백필 시 덜 채워진 티커 추적용)
SKIPPED_COVERAGE_OUTLIER_THRESHOLD = 0.9

_client = get_client()


def collect_one(
    symbol: str,
    market: str,
    name: str,
    start_date: str,
    end_date: str,
    *,
    use_existing: bool = True,
    coverage_threshold: float = 0.7,
) -> tuple[str, bool, str | None]:
    """
    요청 구간을 chunk_days 단위로 나누고, 기존 silver에 이미 충분히 있는 청크는 API 호출 없이
    재사용하고, 부족한 청크만 API 호출 후 합쳐 저장한다.
    """
    try:
        chunk_days = settings.backfill_chunk_days
        existing = (
            parquet_store.load_symbol_range(symbol, market, start_date, end_date)
            if use_existing
            else None
        )
        n_existing = 0 if existing is None else len(existing)
        combined, raw_payloads, skipped_with_ratio = fetch_ohlcv_chunked(
            _client, symbol, market, start_date, end_date,
            chunk_days=chunk_days,
            existing_df=existing if n_existing > 0 else None,
            coverage_threshold=coverage_threshold,
        )
        for c_start, c_end, ratio in skipped_with_ratio:
            if ratio < SKIPPED_COVERAGE_OUTLIER_THRESHOLD:
                log.warning(
                    "backfill skipped chunk low coverage %s %s..%s ratio=%.3f (threshold=%.2f)",
                    symbol, c_start, c_end, ratio, coverage_threshold,
                )
        for c_start, _c_end, payload in raw_payloads:
            parquet_store.save_raw_json(symbol, payload, suffix=c_start.replace("-", ""))
        df = validate_ohlcv(combined)
        parquet_store.upsert_ohlcv_from_df(df)
        last_date = None if df.empty else df["date"].max().strftime("%Y-%m-%d")
        meta_store.upsert_collect_state(symbol, True, last_date, None)
        n_rows = len(df)
        n_fetched = len(raw_payloads)
        _s = datetime.fromisoformat(start_date)
        _e = datetime.fromisoformat(end_date)
        total_chunks = max(1, (_e - _s).days // chunk_days + 1)
        n_skipped = total_chunks - n_fetched
        log.info(
            "backfill ok %s rows=%s fetched_chunks=%s skipped_chunks=%s chunk_days=%s",
            symbol, n_rows, n_fetched, n_skipped, chunk_days,
        )
        return symbol, True, None
    except Exception as e:
        meta_store.upsert_collect_state(symbol, False, None, str(e))
        return symbol, False, str(e)


def run_backfill(rows, start_date: str, end_date: str) -> tuple[int, int]:
    """
    정책상 KIS API는 단일 세션(단일 토큰)에서 순차 수집만 허용된다고 가정하고,
    백필도 순차적으로 수행한다.
    """
    success = 0
    failed = 0
    for r in rows:
        symbol = str(r["symbol"])
        market = str(r["market"])
        name = str(r["name"])
        sym, ok, err = collect_one(symbol, market, name, start_date, end_date)
        if ok:
            success += 1
        else:
            failed += 1
            log.error("backfill fail %s: %s", sym, err)
    return success, failed
