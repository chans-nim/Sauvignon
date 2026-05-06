"""KISClient와 동일 인터페이스의 목 클라이언트 (API 없이 전체 플로우 검증)."""

from __future__ import annotations

import random
import time
from collections.abc import Iterator
from typing import Any

import numpy as np
import pandas as pd


class MockClient:
    """스캐너 데모용 가짜 시장 데이터 (대형 유니버스 + 상관된 수익률)."""

    def __init__(self, seed: int = 42, *, n_stocks: int = 400, n_groups: int = 20) -> None:
        self._rng = random.Random(seed)
        self._token: str | None = "MOCK_TOKEN"
        self.n_stocks = max(50, int(n_stocks))
        self.n_groups = max(3, int(n_groups))
        self._stocks: dict[str, dict[str, Any]] = {}
        self._price_frame = pd.DataFrame()
        self._sector_labels = [
            ("001", "반도체"),
            ("002", "2차전지"),
            ("003", "바이오"),
            ("004", "금융"),
            ("005", "조선"),
            ("006", "자동차"),
            ("007", "철강"),
            ("008", "화학"),
            ("009", "유통"),
            ("010", "건설"),
            ("011", "통신"),
            ("012", "엔터"),
            ("013", "게임"),
            ("014", "IT서비스"),
            ("015", "기계"),
            ("016", "운송"),
            ("017", "에너지"),
            ("018", "부동산"),
            ("019", "의료기기"),
            ("020", "식품"),
            ("021", "방산"),
            ("022", "디스플레이"),
            ("023", "조선부품"),
            ("024", "제지"),
            ("025", "정유"),
            ("026", "레저"),
            ("027", "항공"),
            ("028", "소프트웨어"),
            ("029", "인프라"),
            ("030", "가전"),
        ]
        self._sectors: list[tuple[str, str, float]] = []
        for i, (code, name) in enumerate(self._sector_labels[: self.n_groups]):
            ret = self._rng.uniform(-0.4, 2.2) / 100.0
            self._sectors.append((code, name, ret * 100))
        self._build_universe()
        self._build_price_panel()

    def _synthetic_kr_name(self, symbol: str, sector_name: str) -> str:
        """데모용 한국식 종목명 (실제 상장명 아님)."""
        try:
            n = int(str(symbol).strip())
        except ValueError:
            n = hash(symbol) % 10_000
        prefs = ("대한", "동양", "한신", "제이", "티", "아이", "미래", "코리아", "삼호", "유니")
        mids = ("테크", "켐", "파워", "시스템", "엔지니어링", "머티리얼", "솔루션", "인더스", "홀딩스", "스틸")
        sec = (sector_name or "일반").strip()[:5]
        p = prefs[n % len(prefs)]
        mid = mids[(n // 7) % len(mids)]
        return f"{p}{sec}{mid}"

    def _build_universe(self) -> None:
        per = max(1, self.n_stocks // self.n_groups)
        idx = 0
        for g in range(self.n_groups):
            code, sec_name, _ = self._sectors[g % len(self._sectors)]
            for j in range(per):
                if idx >= self.n_stocks:
                    break
                sym = f"{300000 + idx:06d}"
                is_leader = j == 0
                base = 8_000 + self._rng.randint(0, 900) * 10
                self._stocks[sym] = {
                    "symbol": sym,
                    "name": self._synthetic_kr_name(sym, sec_name),
                    "sector_code": code,
                    "sector_name": sec_name,
                    "base_price": float(base),
                    "group_id": g,
                    "is_leader": is_leader,
                    "value_traded": float(self._rng.randint(120_000_000, 2_200_000_000)),
                }
                idx += 1
        while len(self._stocks) < self.n_stocks:
            sym = f"{300000 + idx:06d}"
            g = idx % self.n_groups
            code, sec_name, _ = self._sectors[g % len(self._sectors)]
            self._stocks[sym] = {
                "symbol": sym,
                "name": self._synthetic_kr_name(sym, sec_name),
                "sector_code": code,
                "sector_name": sec_name,
                "base_price": float(10_000 + self._rng.randint(0, 500) * 10),
                "group_id": g,
                "is_leader": False,
                "value_traded": float(self._rng.randint(100_000_000, 1_800_000_000)),
            }
            idx += 1

    def _build_price_panel(self) -> None:
        days = 90
        dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=days)
        syms = sorted(self._stocks.keys())
        prices = pd.DataFrame(index=dates, columns=syms, dtype=float)
        prev = {s: float(self._stocks[s]["base_price"]) for s in syms}
        group_ids = {s: int(self._stocks[s]["group_id"]) for s in syms}
        for t in range(len(dates)):
            d = dates[t]
            for g in range(self.n_groups):
                gr_ret = self._rng.gauss(0.00025 + 0.00008 * g, 0.011)
                for s in syms:
                    if group_ids[s] != g:
                        continue
                    leader_boost = 1.35 if self._stocks[s].get("is_leader") else 1.0
                    r = gr_ret * leader_boost + self._rng.gauss(0.0, 0.0075)
                    px = prev[s] * (1.0 + r)
                    prev[s] = px
                    prices.loc[d, s] = px
        self._price_frame = prices

    def authenticate(self) -> None:
        self._token = "MOCK_TOKEN"

    def is_authenticated(self) -> bool:
        return self._token is not None

    def fetch_universe_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for sym, meta in self._stocks.items():
            rows.append(
                {
                    "symbol": sym,
                    "name": meta["name"],
                    "market": "KOSPI" if self._rng.random() < 0.55 else "KOSDAQ",
                    "value_traded": float(meta.get("value_traded", 0.0)),
                    "sector_code": meta["sector_code"],
                    "sector_name": meta["sector_name"],
                }
            )
        return rows

    def fetch_price_history_frame(self, symbols: list[str], *, days: int = 60) -> pd.DataFrame:
        syms = [s for s in symbols if s in self._price_frame.columns]
        if not syms:
            return pd.DataFrame()
        d = max(10, int(days))
        return self._price_frame[syms].iloc[-d:].copy()

    def fetch_sector_current_index(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for code, name, ret in self._sectors:
            idx = 1000.0 + self._rng.uniform(-20, 80) + ret * 10
            out.append(
                {
                    "sector_code": code,
                    "sector_name": name,
                    "current_index": round(idx, 2),
                    "return_pct": ret / 100.0,
                    "high_index": round(idx * 1.012, 2),
                    "low_index": round(idx * 0.985, 2),
                }
            )
        return out

    def fetch_sector_intraday_index(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for code, name, ret in self._sectors:
            drift = ret / 100.0 * 0.5 + self._rng.uniform(-0.002, 0.004)
            out.append(
                {
                    "sector_code": code,
                    "sector_name": name,
                    "intraday_change_pct": drift,
                    "last_bar_return_pct": self._rng.uniform(-0.001, 0.003),
                }
            )
        return out

    def fetch_ranking_volume(self) -> list[dict[str, Any]]:
        return self._ranking_rows("volume", key=lambda s: self._rng.random() * 1e6 + hash(s) % 1000)

    def fetch_ranking_return(self) -> list[dict[str, Any]]:
        return self._ranking_rows("return", key=lambda s: self._rng.uniform(-0.03, 0.06))

    def fetch_ranking_trade_strength(self) -> list[dict[str, Any]]:
        return self._ranking_rows("trade_strength", key=lambda s: self._rng.uniform(0.4, 1.0))

    def fetch_ranking_near_high(self) -> list[dict[str, Any]]:
        return self._ranking_rows("near_high", key=lambda s: self._rng.uniform(0.5, 1.0))

    def fetch_ranking_block_trades(self) -> list[dict[str, Any]]:
        return self._ranking_rows("block", key=lambda s: self._rng.uniform(0.0, 0.4))

    def _ranking_rows(self, kind: str, key) -> list[dict[str, Any]]:
        syms = list(self._stocks.keys())
        self._rng.shuffle(syms)
        take = min(140, max(40, len(syms) // 3))
        rows: list[dict[str, Any]] = []
        for i, sym in enumerate(syms[:take]):
            meta = self._stocks[sym]
            rows.append(
                {
                    "symbol": sym,
                    "name": meta["name"],
                    "rank": i + 1,
                    "sector_code": meta["sector_code"],
                    "sector_name": meta["sector_name"],
                    "metric": key(sym),
                    "source": f"rank_{kind}",
                }
            )
        return rows

    def fetch_stock_price(self, symbol: str) -> dict[str, Any]:
        meta = self._stocks.get(symbol)
        if not meta:
            return {}
        px = meta["base_price"] * (1.0 + self._rng.uniform(-0.015, 0.025))
        open_px = meta["base_price"] * (1.0 + self._rng.uniform(-0.008, 0.008))
        high = max(open_px, px) * (1.0 + self._rng.uniform(0, 0.012))
        low = min(open_px, px) * (1.0 - self._rng.uniform(0, 0.012))
        vol = float(self._rng.randint(800_000, 5_000_000))
        return {
            "symbol": symbol,
            "name": meta["name"],
            "price": round(px, 2),
            "open": round(open_px, 2),
            "high": round(high, 2),
            "low": round(low, 2),
            "close": round(px, 2),
            "volume": vol,
            "value_traded": round(px * vol, 2),
        }

    def fetch_stock_intraday_bars(self, symbol: str) -> list[dict[str, Any]]:
        meta = self._stocks.get(symbol)
        if not meta:
            return []
        base = meta["base_price"]
        bars: list[dict[str, Any]] = []
        t0 = pd.Timestamp.now(tz="Asia/Seoul").normalize().replace(hour=9, minute=0)
        p = base
        for i in range(30):
            ts = t0 + pd.Timedelta(minutes=i + 1)
            o = p
            c = p * (1.0 + self._rng.uniform(-0.002, 0.003))
            h = max(o, c) * (1.0 + self._rng.uniform(0, 0.0015))
            l = min(o, c) * (1.0 - self._rng.uniform(0, 0.0015))
            v = float(self._rng.randint(5_000, 80_000))
            bars.append(
                {
                    "ts": ts.isoformat(),
                    "open": round(o, 2),
                    "high": round(h, 2),
                    "low": round(l, 2),
                    "close": round(c, 2),
                    "volume": v,
                }
            )
            p = c
        return bars

    def fetch_program_flow(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for code, name, _ in self._sectors[: min(6, len(self._sectors))]:
            rows.append(
                {
                    "sector_code": code,
                    "sector_name": name,
                    "program_net_strength": self._rng.uniform(-0.2, 0.9),
                }
            )
        for sym, meta in list(self._stocks.items())[: min(40, len(self._stocks))]:
            rows.append(
                {
                    "symbol": sym,
                    "name": meta["name"],
                    "program_net_strength": self._rng.uniform(-0.15, 0.85),
                }
            )
        return rows

    def fetch_foreign_institution_flow(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for sym, meta in self._stocks.items():
            fn = self._rng.uniform(-400, 700) * 1e9
            ins = self._rng.uniform(-300, 600) * 1e9
            rows.append(
                {
                    "symbol": sym,
                    "name": meta["name"],
                    "foreign_net_tr_pbmn": fn,
                    "institution_net_tr_pbmn": ins,
                    "foreign_net_strength": self._rng.uniform(-0.4, 0.7),
                    "institution_net_strength": self._rng.uniform(-0.3, 0.6),
                }
            )
        return rows

    def fetch_program_trade_net_for_symbol(self, symbol: str) -> dict[str, Any]:
        return {"symbol": symbol, "program_net_tr_pbmn": self._rng.uniform(-50, 120) * 1e9}

    def stream_ticks(self, symbols: list[str]) -> Iterator[dict[str, Any]]:
        """심볼별 랜덤 워크 체결 이벤트."""
        if not symbols:
            return
        for _ in range(min(5, max(1, len(symbols) * 2))):
            sym = symbols[self._rng.randrange(0, len(symbols))]
            meta = self._stocks.get(sym, {"base_price": 70000.0})
            px = float(meta["base_price"]) * (1.0 + self._rng.uniform(-0.002, 0.002))
            yield {
                "symbol": sym,
                "price": round(px, 2),
                "volume": float(self._rng.randint(10, 5000)),
                "ts": pd.Timestamp.now(tz="Asia/Seoul").isoformat(),
            }
            time.sleep(0.01)
