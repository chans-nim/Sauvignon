from __future__ import annotations

import pytest

from src.master.parse_kospi_mst import parse_row as parse_kospi
from src.master.parse_kosdaq_mst import parse_row as parse_kosdaq


@pytest.mark.parametrize("parser,width,halt,admin,listing", [
    (parse_kospi, 227, 60, 62, 105),
    (parse_kosdaq, 221, 55, 57, 100),
])
@pytest.mark.parametrize("ending", ["\n", "\r\n", ""])
def test_master_preserves_name_and_exchange_status(parser, width, halt, admin, listing, ending):
    tail = list(" " * width)
    tail[halt] = "Y"
    tail[admin] = "Y"
    tail[listing:listing + 8] = list("20260930")
    row = parser("0007C0   " + "KR7000000000" + "테스트종목" + "".join(tail) + ending)
    assert row["symbol"] == "0007C0"
    assert row["name"] == "테스트종목"
    assert row["is_trading_halt"] is True
    assert row["is_admin_issue"] is True
    assert row["listing_date"] == "20260930"
