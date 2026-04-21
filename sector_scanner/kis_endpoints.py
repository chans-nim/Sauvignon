"""
KIS Open API paths and TR IDs used by sector_scanner (aligned with 한국투자 open-trading-api examples).

See: https://github.com/koreainvestment/open-trading-api
"""

from __future__ import annotations

# REST base (override via settings / env if needed)
KIS_REST_BASE_URL: str = "https://openapi.koreainvestment.com:9443"
KIS_WS_URL: str = "wss://openapi.koreainvestment.com:9443/websocket"

# --- Domestic index / sector ---
URL_INDEX_CATEGORY_PRICE: str = "/uapi/domestic-stock/v1/quotations/inquire-index-category-price"
TR_INDEX_CATEGORY_PRICE: str = "FHPUP02140000"

URL_INDEX_PRICE: str = "/uapi/domestic-stock/v1/quotations/inquire-index-price"
TR_INDEX_PRICE: str = "FHPUP02100000"

URL_INDEX_TIMEPRICE: str = "/uapi/domestic-stock/v1/quotations/inquire-time-indexchartprice"
TR_INDEX_TIMEPRICE: str = "FHKUP03500200"

# --- Rankings ---
URL_VOLUME_RANK: str = "/uapi/domestic-stock/v1/quotations/volume-rank"
TR_VOLUME_RANK: str = "FHPST01710000"

URL_FLUCTUATION_RANK: str = "/uapi/domestic-stock/v1/ranking/fluctuation"
TR_FLUCTUATION_RANK: str = "FHPST01700000"

URL_VOLUME_POWER: str = "/uapi/domestic-stock/v1/ranking/volume-power"
TR_VOLUME_POWER: str = "FHPST01680000"

URL_NEAR_HIGHLOW: str = "/uapi/domestic-stock/v1/ranking/near-new-highlow"
TR_NEAR_HIGHLOW: str = "FHPST01870000"

# --- Stock quote & intraday ---
URL_INQUIRE_PRICE: str = "/uapi/domestic-stock/v1/quotations/inquire-price"
TR_INQUIRE_PRICE: str = "FHKST01010100"

URL_TIME_ITEMCHART: str = "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
TR_TIME_ITEMCHART: str = "FHKST03010200"

# --- Flow (optional; sector_loader tolerates empty) ---
URL_FRGN_INST_TOTAL: str = "/uapi/domestic-stock/v1/quotations/foreign-institution-total"
TR_FRGN_INST_TOTAL: str = "FHPTJ04400000"
