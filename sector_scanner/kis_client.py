"""KIS REST 연동: 업종 지수·순위·종목 시세 (sector_scanner / run_scanner --mode real|paper)."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from src.clients.kis_client import KISClient as KISAuthClient
from src.clients.kis_client import KISConfig
from src.common.settings import settings

from . import kis_endpoints as ep

logger = logging.getLogger(__name__)


def _upper_map(d: dict[str, Any]) -> dict[str, Any]:
    return {str(k).upper(): v for k, v in d.items()}


def _pick(u: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        v = u.get(k.upper())
        if v is None:
            continue
        s = str(v).strip()
        if s == "" or s == "None":
            continue
        return v
    return None


def _to_float(x: Any, default: float = 0.0) -> float:
    if x is None:
        return default
    try:
        s = str(x).strip().replace(",", "").replace("%", "")
        if s in {"", "-", "None"}:
            return default
        return float(s)
    except (TypeError, ValueError):
        return default


def _coerce_output_list(payload: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    for k in keys:
        v = payload.get(k)
        if v is None:
            continue
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
        if isinstance(v, dict):
            return [v]
    return []


def _norm_stock_quote(out0: dict[str, Any]) -> dict[str, Any]:
    u = _upper_map(out0)
    price = _pick(u, "STCK_PRPR", "PRPR", "STCK_CLPR")
    prev_close = _to_float(_pick(u, "STCK_PRDY_CLPR", "PRDY_CLPR"))
    return_pct = _to_float(_pick(u, "PRDY_CTRT", "FLUC_RT", "CTRT"))
    if abs(return_pct) > 0.35:
        return_pct = return_pct / 100.0
    return {
        "symbol": str(_pick(u, "MKSC_SHRN_ISCD", "ISCD") or "").strip(),
        "name": str(_pick(u, "HTS_KOR_ISNM", "PRDT_NAME") or "").strip(),
        "price": _to_float(price),
        "open": _to_float(_pick(u, "STCK_OPRC", "OPRC")),
        "high": _to_float(_pick(u, "STCK_HGPR", "HGPR")),
        "low": _to_float(_pick(u, "STCK_LWPR", "LWPR")),
        "close": _to_float(_pick(u, "STCK_CLPR", "STCK_PRPR")),
        "previous_close": prev_close,
        "return_pct": return_pct,
        "volume": _to_float(_pick(u, "ACML_VOL", "CNTG_VOL")),
        "value_traded": _to_float(_pick(u, "ACML_TR_PBMN", "PBMN")),
    }


def _norm_rank_row(row: dict[str, Any], *, source: str, metric_keys: tuple[str, ...]) -> dict[str, Any] | None:
    u = _upper_map(row)
    sym = str(_pick(u, "MKSC_SHRN_ISCD", "SHOTN_ISCD", "ISCD", "PDNO") or "").strip()
    if not sym:
        return None
    name = str(_pick(u, "HTS_KOR_ISNM", "PRDT_ABRV_NAME") or sym).strip()
    rk = _pick(u, "DATA_RANK", "RANK")
    rank_i: int | None = None
    if rk is not None:
        try:
            rank_i = int(str(rk).strip())
        except ValueError:
            rank_i = None
    metric = 0.0
    for mk in metric_keys:
        v = u.get(mk.upper())
        if v is not None:
            metric = _to_float(v)
            break
    return {
        "symbol": sym,
        "name": name,
        "rank": rank_i,
        "metric": metric,
        "source": source,
    }


def _sector_code_from_row(u: dict[str, Any]) -> str:
    for k in (
        "BSTP_CLS_CODE",
        "BSTP_LCLSCD",
        "IDX_SHRN_ISCD",
        "ISCD",
        "MKSC_SHRN_ISCD",
        "IDX_CODE",
    ):
        v = _pick(u, k)
        if v is None:
            continue
        s = str(v).strip()
        if len(s) >= 3:
            return s[:5] if len(s) > 5 else s
    return ""


def _sector_name_from_row(u: dict[str, Any]) -> str:
    v = _pick(u, "HTS_KOR_ISNM", "IDX_NAME", "BSTP_KOR_ISNM")
    return str(v or "").strip()


def _norm_sector_current_row(
    row: dict[str, Any],
    *,
    fallback_code: str = "",
    fallback_name: str = "",
) -> dict[str, Any] | None:
    u = _upper_map(row)
    code = _sector_code_from_row(u) or str(fallback_code).strip()
    if not code:
        return None
    name = _sector_name_from_row(u) or str(fallback_name).strip() or code
    cur = _to_float(
        _pick(
            u,
            "BSTP_NMIX_PRPR",
            "IDX_CLPR",
            "IDX_INDX",
            "PRDY_CLPR",
            "STCK_PRPR",
            "PRPR",
        )
    )
    chg_pct = _to_float(_pick(u, "BSTP_NMIX_PRDY_CTRT", "PRDY_CTRT", "FLUC_RT", "CTRT"))
    if abs(chg_pct) > 0.35:
        chg_pct = chg_pct / 100.0
    hi = _pick(u, "BSTP_NMIX_HGPR", "PRDY_HGPR", "HGPR", "IDX_HGPR")
    lo = _pick(u, "BSTP_NMIX_LWPR", "PRDY_LWPR", "LWPR", "IDX_LWPR")
    return {
        "sector_code": code,
        "sector_name": name,
        "current_index": cur,
        "return_pct": chg_pct,
        "high_index": float(_to_float(hi)) if hi is not None else None,
        "low_index": float(_to_float(lo)) if lo is not None else None,
    }


def _extract_intraday_summary(
    body: dict[str, Any],
    *,
    fallback_code: str,
    fallback_name: str = "",
) -> dict[str, Any] | None:
    meta_rows = _coerce_output_list(body, "output1", "output")
    bar_rows = _coerce_output_list(body, "output2")
    meta_u = _upper_map(meta_rows[0]) if meta_rows else {}
    code = _sector_code_from_row(meta_u) or str(fallback_code).strip()
    name = _sector_name_from_row(meta_u) or str(fallback_name).strip() or code
    prices: list[float] = []
    last_bar = 0.0
    prev_close = _to_float(_pick(meta_u, "PRDY_NMIX", "PRDY_CLPR", "BSTP_NMIX_PRDY_VRSS"))
    for row in bar_rows:
        if not isinstance(row, dict):
            continue
        u = _upper_map(row)
        p = _to_float(_pick(u, "BSTP_NMIX_PRPR", "IDX_CLPR", "STCK_PRPR", "PRPR"))
        if p > 0:
            prices.append(p)
    if len(prices) >= 2 and prices[0] > 0:
        intraday = prices[-1] / prices[0] - 1.0
    else:
        intraday = 0.0
    if prices and prev_close > 0:
        last_bar = prices[-1] / prev_close - 1.0
    return {
        "sector_code": code,
        "sector_name": name,
        "intraday_change_pct": float(intraday),
        "last_bar_return_pct": float(last_bar),
    }


class KISClient:
    """
    sector_scanner용 KIS REST 클라이언트 (MockClient와 동일 메서드 시그니처).

    - 업종 스냅샷: ``inquire-index-category-price`` (코스피/코스닥) + 보조 ``inquire-index-price``
    - 순위: 거래량/등락률/체결강도/신고가 근접 (업종 필터는 거래량 순위에 ``FID_INPUT_ISCD`` 로 지정)
    - 종목 시세: ``inquire-price``, 분봉: ``inquire-time-itemchartprice``
    """

    def __init__(
        self,
        *,
        auth: KISAuthClient | None = None,
        market_div: str = "J",
        min_interval_sec: float = 0.22,
    ) -> None:
        self._kis = auth or KISAuthClient()
        self._market_div = market_div
        self._min_interval_sec = float(min_interval_sec)
        self._last_call_ts: float = 0.0
        self._token: str | None = None

    def _throttle(self) -> None:
        now = time.monotonic()
        gap = now - self._last_call_ts
        if gap < self._min_interval_sec:
            time.sleep(self._min_interval_sec - gap)
        self._last_call_ts = time.monotonic()

    def _get(self, path: str, params: dict[str, Any], tr_id: str) -> dict[str, Any]:
        self._throttle()
        return self._kis.request_get(path, params, tr_id)

    def authenticate(self) -> None:
        self._kis.issue_token()
        self._token = "ok"

    def is_authenticated(self) -> bool:
        return self._token is not None

    # --- Sector index ---

    def fetch_sector_current_index(self) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        specs = [
            ("U", "0001", "20214", "K", "0"),
            ("U", "1001", "20214", "Q", "0"),
        ]
        for fid_cond_mrkt, fid_input, fid_scr, fid_mrkt, fid_blng in specs:
            try:
                body = self._get(
                    ep.URL_INDEX_CATEGORY_PRICE,
                    {
                        "FID_COND_MRKT_DIV_CODE": fid_cond_mrkt,
                        "FID_INPUT_ISCD": fid_input,
                        "FID_COND_SCR_DIV_CODE": fid_scr,
                        "FID_MRKT_CLS_CODE": fid_mrkt,
                        "FID_BLNG_CLS_CODE": fid_blng,
                    },
                    ep.TR_INDEX_CATEGORY_PRICE,
                )
                for row in _coerce_output_list(body, "output1", "output"):
                    if not isinstance(row, dict):
                        continue
                    n = _norm_sector_current_row(row)
                    if n:
                        merged[str(n["sector_code"]).strip()] = n
            except Exception as e:
                logger.warning("inquire-index-category-price skip %s: %s", (fid_input, fid_mrkt), e)

        if not merged:
            for fid_input in ("0001", "1001"):
                try:
                    body = self._get(
                        ep.URL_INDEX_PRICE,
                        {
                            "FID_COND_MRKT_DIV_CODE": "U",
                            "FID_INPUT_ISCD": fid_input,
                        },
                        ep.TR_INDEX_PRICE,
                    )
                    for row in _coerce_output_list(body, "output", "output1"):
                        if not isinstance(row, dict):
                            continue
                        n = _norm_sector_current_row(row, fallback_code=fid_input, fallback_name=fid_input)
                        if n:
                            merged[str(n["sector_code"]).strip()] = n
                except Exception as e:
                    logger.warning("inquire-index-price skip %s: %s", fid_input, e)

        return list(merged.values())

    def fetch_sector_intraday_index(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for fid_input in ("0001", "1001"):
            try:
                body = self._get(
                    ep.URL_INDEX_TIMEPRICE,
                    {
                        "FID_COND_MRKT_DIV_CODE": "U",
                        "FID_ETC_CLS_CODE": "0",
                        "FID_INPUT_ISCD": fid_input,
                        "FID_INPUT_HOUR_1": "60",
                        "FID_PW_DATA_INCU_YN": "Y",
                    },
                    ep.TR_INDEX_TIMEPRICE,
                )
                summary = _extract_intraday_summary(body, fallback_code=fid_input, fallback_name=fid_input)
                if summary:
                    out.append(summary)
            except Exception as e:
                logger.warning("inquire-index-timeprice skip %s: %s", fid_input, e)
        return out

    def fetch_sector_snapshot_by_code(self, sector_code: str) -> dict[str, Any] | None:
        """
        단일 업종코드(예: 0008, 0029, 1023)에 대해 현재지수 + 분봉 추세를 조회한다.
        반환 키는 sector_loader/collect_sector_data가 바로 쓸 수 있는 정규화 형태다.
        """
        sc = str(sector_code).strip()
        if not sc:
            return None
        current_body = self._get(
            ep.URL_INDEX_PRICE,
            {
                "FID_COND_MRKT_DIV_CODE": "U",
                "FID_INPUT_ISCD": sc,
            },
            ep.TR_INDEX_PRICE,
        )
        current_rows = _coerce_output_list(current_body, "output", "output1")
        if not current_rows:
            return None
        current = _norm_sector_current_row(current_rows[0], fallback_code=sc, fallback_name=sc)
        if not current:
            return None
        try:
            intraday_body = self._get(
                ep.URL_INDEX_TIMEPRICE,
                {
                    "FID_COND_MRKT_DIV_CODE": "U",
                    "FID_ETC_CLS_CODE": "0",
                    "FID_INPUT_ISCD": sc,
                    "FID_INPUT_HOUR_1": "60",
                    "FID_PW_DATA_INCU_YN": "Y",
                },
                ep.TR_INDEX_TIMEPRICE,
            )
            intraday = _extract_intraday_summary(
                intraday_body,
                fallback_code=str(current.get("sector_code") or sc),
                fallback_name=str(current.get("sector_name") or sc),
            )
        except Exception as e:
            logger.debug("fetch_sector_snapshot_by_code intraday %s: %s", sc, e)
            intraday = None
        out = dict(current)
        intra = float((intraday or {}).get("intraday_change_pct", 0.0))
        last_bar = float((intraday or {}).get("last_bar_return_pct", 0.0))
        out["intraday_change_pct"] = intra
        out["last_bar_return_pct"] = last_bar
        out["intraday_trend"] = intra if intra != 0.0 else last_bar
        out["acceleration"] = last_bar - intra * 0.5
        return out

    # --- Rankings (market or sector-scoped via fid_input_iscd) ---

    def _volume_rank_params(self, fid_input_iscd: str) -> dict[str, Any]:
        return {
            "FID_COND_MRKT_DIV_CODE": self._market_div,
            "FID_COND_SCR_DIV_CODE": "20171",
            "FID_INPUT_ISCD": fid_input_iscd,
            "FID_DIV_CLS_CODE": "0",
            "FID_BLNG_CLS_CODE": "0",
            "FID_TRGT_CLS_CODE": "111111111",
            "FID_TRGT_EXLS_CLS_CODE": "0000000000",
            "FID_INPUT_PRICE_1": "0",
            "FID_INPUT_PRICE_2": "10000000",
            "FID_VOL_CNT": "0",
            "FID_INPUT_DATE_1": "",
        }

    def fetch_ranking_volume(self) -> list[dict[str, Any]]:
        try:
            body = self._get(ep.URL_VOLUME_RANK, self._volume_rank_params("0000"), ep.TR_VOLUME_RANK)
            rows = _coerce_output_list(body, "output", "output1")
            out: list[dict[str, Any]] = []
            for row in rows:
                n = _norm_rank_row(row, source="rank_volume", metric_keys=("ACML_VOL", "VOL_TNRT", "PRDY_VOL"))
                if n:
                    out.append(n)
            return out
        except Exception as e:
            logger.warning("volume-rank: %s", e)
            return []

    def fetch_ranking_return(self) -> list[dict[str, Any]]:
        try:
            # KIS fluctuation(v1_국내주식-088): FID_* 대문자 권장, TRGT 9자리·EXLS 10자리(미만이면 OPSQ2002 등).
            params = {
                "FID_RSFL_RATE2": "30",
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_COND_SCR_DIV_CODE": "20170",
                "FID_INPUT_ISCD": "0000",
                "FID_RANK_SORT_CLS_CODE": "0000",
                "FID_INPUT_CNT_1": "30",
                "FID_PRC_CLS_CODE": "0",
                "FID_INPUT_PRICE_1": "0",
                "FID_INPUT_PRICE_2": "10000000",
                "FID_VOL_CNT": "0",
                "FID_TRGT_CLS_CODE": "111111111",
                "FID_TRGT_EXLS_CLS_CODE": "0000000000",
                "FID_DIV_CLS_CODE": "0",
                "FID_RSFL_RATE1": "-30",
            }
            body = self._get(ep.URL_FLUCTUATION_RANK, params, ep.TR_FLUCTUATION_RANK)
            rows = _coerce_output_list(body, "output", "output1")
            out: list[dict[str, Any]] = []
            for row in rows:
                n = _norm_rank_row(row, source="rank_return", metric_keys=("PRDY_CTRT", "FLUC_RT", "CTRT"))
                if n:
                    out.append(n)
            return out
        except Exception as e:
            logger.warning("fluctuation rank: %s", e)
            return []

    def fetch_ranking_trade_strength(self) -> list[dict[str, Any]]:
        try:
            params = {
                "fid_trgt_exls_cls_code": "0",
                "fid_cond_mrkt_div_code": "J",
                "fid_cond_scr_div_code": "20168",
                "fid_input_iscd": "0000",
                "fid_div_cls_code": "0",
                "fid_input_price_1": "",
                "fid_input_price_2": "",
                "fid_vol_cnt": "",
                "fid_trgt_cls_code": "0",
            }
            body = self._get(ep.URL_VOLUME_POWER, params, ep.TR_VOLUME_POWER)
            rows = _coerce_output_list(body, "output", "output1")
            out: list[dict[str, Any]] = []
            for row in rows:
                n = _norm_rank_row(
                    row,
                    source="rank_trade_strength",
                    metric_keys=("VOL_TNRT", "CTB_TRSTN", "SELN_CNTG_CSNU", "SHNU_CNTG_CSNU"),
                )
                if n:
                    m = float(n.get("metric") or 0.0)
                    n["metric"] = min(1.0, max(0.0, m / 200.0))
                    out.append(n)
            return out
        except Exception as e:
            logger.warning("volume-power: %s", e)
            return []

    def fetch_ranking_near_high(self) -> list[dict[str, Any]]:
        try:
            params = {
                "fid_aply_rang_vol": "0",
                "fid_cond_mrkt_div_code": "J",
                "fid_cond_scr_div_code": "20187",
                "fid_div_cls_code": "0",
                "fid_input_cnt_1": "0",
                "fid_input_cnt_2": "100",
                "fid_prc_cls_code": "0",
                "fid_input_iscd": "0000",
                "fid_trgt_cls_code": "0",
                "fid_trgt_exls_cls_code": "0",
                "fid_aply_rang_prc_1": "0",
                "fid_aply_rang_prc_2": "10000000",
            }
            body = self._get(ep.URL_NEAR_HIGHLOW, params, ep.TR_NEAR_HIGHLOW)
            rows = _coerce_output_list(body, "output", "output1")
            out: list[dict[str, Any]] = []
            for row in rows:
                n = _norm_rank_row(row, source="rank_near_high", metric_keys=("PRDY_CTRT", "FLUC_RT", "PRDY_VRSS"))
                if n:
                    n["metric"] = min(1.0, max(0.0, abs(float(n.get("metric") or 0.0)) / 30.0))
                    out.append(n)
            return out
        except Exception as e:
            logger.warning("near-new-highlow: %s", e)
            return []

    def fetch_ranking_block_trades(self) -> list[dict[str, Any]]:
        return []

    # --- Stock ---

    def fetch_stock_price(self, symbol: str) -> dict[str, Any]:
        body = self._get(
            ep.URL_INQUIRE_PRICE,
            {
                "FID_COND_MRKT_DIV_CODE": self._market_div,
                "FID_INPUT_ISCD": str(symbol).strip(),
            },
            ep.TR_INQUIRE_PRICE,
        )
        out0 = body.get("output")
        if isinstance(out0, list) and out0:
            out0 = out0[0]
        if not isinstance(out0, dict):
            return {}
        return _norm_stock_quote(out0)

    def fetch_stock_intraday_bars(self, symbol: str) -> list[dict[str, Any]]:
        bars: list[dict[str, Any]] = []
        hour = "090000"
        for _ in range(12):
            try:
                body = self._get(
                    ep.URL_TIME_ITEMCHART,
                    {
                        "FID_COND_MRKT_DIV_CODE": self._market_div,
                        "FID_INPUT_ISCD": str(symbol).strip(),
                        "FID_INPUT_HOUR_1": hour,
                        "FID_PW_DATA_INCU_YN": "Y",
                        "FID_ETC_CLS_CODE": "",
                    },
                    ep.TR_TIME_ITEMCHART,
                )
                part = _coerce_output_list(body, "output2", "output")
                if not part:
                    break
                for r in part:
                    if not isinstance(r, dict):
                        continue
                    u = _upper_map(r)
                    bars.append(
                        {
                            "ts": str(_pick(u, "STCK_CNTG_HOUR", "CNTG_HOUR", "BSOP_DATE") or ""),
                            "open": _to_float(_pick(u, "STCK_OPRC", "OPRC")),
                            "high": _to_float(_pick(u, "STCK_HGPR", "HGPR")),
                            "low": _to_float(_pick(u, "STCK_LWPR", "LWPR")),
                            "close": _to_float(_pick(u, "STCK_PRPR", "STCK_CLPR", "PRPR")),
                            "volume": _to_float(_pick(u, "CNTG_VOL", "ACML_VOL")),
                        }
                    )
                last_u = _upper_map(part[-1])
                nh = str(_pick(last_u, "STCK_CNTG_HOUR", "CNTG_HOUR") or "")
                if nh and nh.isdigit() and len(nh) >= 6:
                    hour = nh
                else:
                    break
            except Exception as e:
                logger.debug("intraday chunk %s %s: %s", symbol, hour, e)
                break
        return bars

    def fetch_program_flow(self) -> list[dict[str, Any]]:
        return []

    def fetch_foreign_institution_flow(self) -> list[dict[str, Any]]:
        try:
            body = self._get(
                ep.URL_FRGN_INST_TOTAL,
                {
                    "FID_COND_MRKT_DIV_CODE": "V",
                    "FID_COND_SCR_DIV_CODE": "16449",
                    "FID_INPUT_ISCD": "0000",
                    "FID_DIV_CLS_CODE": "0",
                    "FID_RANK_SORT_CLS_CODE": "0",
                    "FID_ETC_CLS_CODE": "0",
                },
                ep.TR_FRGN_INST_TOTAL,
            )
            rows = _coerce_output_list(body, "output", "output1")
            out: list[dict[str, Any]] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                u = _upper_map(row)
                sym = str(_pick(u, "MKSC_SHRN_ISCD", "ISCD", "PDNO") or "").strip()
                if not sym:
                    continue
                name = str(_pick(u, "HTS_KOR_ISNM") or "").strip()
                fn = _to_float(_pick(u, "FRGN_NTBY_QTY", "FRGN_NET", "FRGN_NTBY_TR_PBMN"))
                ins = _to_float(_pick(u, "ORGNT_NTBY_QTY", "INST_NET", "ORGNT_NTBY_TR_PBMN"))
                out.append(
                    {
                        "symbol": sym,
                        "name": name,
                        "foreign_net_strength": min(1.0, max(-1.0, fn / 1e6)),
                        "institution_net_strength": min(1.0, max(-1.0, ins / 1e6)),
                    }
                )
            return out
        except Exception as e:
            logger.debug("foreign-institution-total: %s", e)
            return []

    def _volume_rank_acml_tr_pbmn_by_symbol(self) -> dict[str, float]:
        """meta에 거래대금이 없을 때 거래량순위 응답의 누적거래대금으로 유동성 프록시."""
        out: dict[str, float] = {}
        try:
            body = self._get(ep.URL_VOLUME_RANK, self._volume_rank_params("0000"), ep.TR_VOLUME_RANK)
            for row in _coerce_output_list(body, "output", "output1"):
                if not isinstance(row, dict):
                    continue
                u = _upper_map(row)
                sym = str(_pick(u, "MKSC_SHRN_ISCD", "ISCD") or "").strip()
                if not sym:
                    continue
                v = _to_float(_pick(u, "ACML_TR_PBMN", "PBMN", "ACML_PRDY_PBMN"))
                if v > 0:
                    out[sym] = v
        except Exception as e:
            logger.debug("volume-rank liquidity proxy: %s", e)
        return out

    def fetch_universe_rows(self) -> list[dict[str, Any]]:
        try:
            from src.storage import meta_store

            meta_store.ensure_tables()
            df = meta_store.load_universe(limit=None)
            if df is None or df.empty:
                return []
            pbmn_map = self._volume_rank_acml_tr_pbmn_by_symbol()
            rows: list[dict[str, Any]] = []
            for _, r in df.iterrows():
                sym = str(r.get("symbol", "")).strip()
                if not sym:
                    continue
                vt = float(r.get("value_traded", 0.0) or 0.0)
                if vt <= 0 and sym in pbmn_map:
                    vt = float(pbmn_map[sym])
                rows.append(
                    {
                        "symbol": sym,
                        "name": str(r.get("name", sym) or sym).strip(),
                        "market": str(r.get("market", "KOSPI") or "KOSPI").strip().upper(),
                        "value_traded": vt,
                    }
                )
            return rows
        except Exception as e:
            logger.warning("fetch_universe_rows: %s", e)
            return []

    def fetch_price_history_frame(self, symbols: list[str], *, days: int = 60) -> Any:
        end = datetime.now().date()
        start = end - timedelta(days=max(10, int(days)))
        start_s = start.strftime("%Y-%m-%d")
        end_s = end.strftime("%Y-%m-%d")
        series_list: list[pd.Series] = []
        idx: pd.DatetimeIndex | None = None
        for sym in symbols:
            sym = str(sym).strip()
            if not sym:
                continue
            try:
                raw = self._kis.get_daily_ohlcv(sym, start_s, end_s, market_div_code="J")
                from src.collect.base_collect import normalize_ohlcv

                mkt = "KOSPI"
                df = normalize_ohlcv(sym, mkt, raw)
                if df.empty:
                    continue
                s = df.set_index("date")["close"].astype(float)
                s.name = sym
                series_list.append(s)
                if idx is None:
                    idx = pd.DatetimeIndex(df["date"].values)
            except Exception as e:
                logger.debug("history %s: %s", sym, e)
                continue
        if not series_list:
            return pd.DataFrame()
        wide = pd.concat(series_list, axis=1)
        wide = wide.sort_index()
        return wide.tail(max(10, int(days)))

    def stream_ticks(self, symbols: list[str]) -> Iterator[dict[str, Any]]:
        if symbols:
            logger.info("stream_ticks: WebSocket not implemented; no events for %d symbols", len(symbols))
        return iter(())

    # --- Extension: members of a sector (업종별 거래량 순위) ---

    def fetch_stocks_in_sector(self, sector_code: str, *, max_pages: int = 3) -> list[dict[str, Any]]:
        """
        ``FID_INPUT_ISCD`` = 업종코드 로 거래량 순위를 조회해 해당 업종 구성 종목을 반환한다.

        Parameters
        ----------
        sector_code:
            KIS/FAQ 업종 코드 (예: ``0002`` 등). 앞자리 0을 포함한 문자열 권장.
        max_pages:
            연속조회(``tr_cont``) 미구현으로, 동일 요청 반복 대신 상한만 둔다.
        """
        sc = str(sector_code).strip()
        if not sc:
            return []
        out: list[dict[str, Any]] = []
        for _ in range(max(1, int(max_pages))):
            try:
                body = self._get(ep.URL_VOLUME_RANK, self._volume_rank_params(sc), ep.TR_VOLUME_RANK)
                rows = _coerce_output_list(body, "output", "output1")
            except Exception as e:
                logger.warning("fetch_stocks_in_sector %s: %s", sc, e)
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                u = _upper_map(row)
                sym = str(_pick(u, "MKSC_SHRN_ISCD", "ISCD") or "").strip()
                if not sym:
                    continue
                out.append(
                    {
                        "symbol": sym,
                        "name": str(_pick(u, "HTS_KOR_ISNM") or "").strip(),
                        "sector_code": sc,
                        "rank": _pick(u, "DATA_RANK", "RANK"),
                        "value_traded": _to_float(_pick(u, "ACML_TR_PBMN", "PBMN")),
                        "volume": _to_float(_pick(u, "ACML_VOL", "VOL_TNRT")),
                        "raw": row,
                    }
                )
            break
        return out


def build_kis_client_for_mode(mode: str) -> KISClient:
    """``paper`` / ``real`` 에 맞춰 KISConfig(app key/secret)를 선택한다."""
    m = (mode or "real").strip().lower()
    if m == "paper":
        if settings.kis_paper_app_key and settings.kis_paper_app_secret:
            cfg = KISConfig(app_key=settings.kis_paper_app_key, app_secret=settings.kis_paper_app_secret)
        else:
            logger.warning("KIS_PAPER_APP_KEY/SECRET missing; falling back to KIS_APP_KEY for paper mode")
            cfg = KISConfig()
    else:
        cfg = KISConfig()
    return KISClient(auth=KISAuthClient(cfg))
