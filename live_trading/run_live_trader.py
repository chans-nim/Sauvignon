"""
Mock broker + synthetic stream demo entrypoint.

실행 (권장): ``cd live_trading`` 후 ``python run_live_trader.py``
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parent
_BT = _ROOT.parent / "backtest_mvp"
for p in (_ROOT, _BT):
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

from config import BacktestConfig  # noqa: E402
from signals import SignalModel  # noqa: E402

from broker_interface import BrokerInterface  # noqa: E402
from live_config import LiveTradingConfig  # noqa: E402
from live_trader import LiveTrader  # noqa: E402
from market_data_handler import MarketDataHandler  # noqa: E402
from models import MarketEvent  # noqa: E402
from mock_broker import MockBroker  # noqa: E402
from order_manager import OrderManager  # noqa: E402
from position_manager import PositionManager  # noqa: E402
from risk_manager import RiskManager  # noqa: E402
from signal_adapter import SignalAdapter  # noqa: E402
from state_store import JSONStateStore  # noqa: E402


def setup_logging(log_path: str) -> logging.Logger:
    """콘솔 + 파일 로깅 설정."""
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root.handlers.clear()
    root.addHandler(ch)

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    return logging.getLogger("run_live_trader")


def build_config() -> LiveTradingConfig:
    base = _ROOT / "state"
    log = _ROOT / "logs" / "live_trader.log"
    return LiveTradingConfig(
        mode="mock",
        symbols=["005930", "000660", "035420", "051910", "006400"],
        max_positions=4,
        allocation_per_trade=5_000_000.0,
        max_daily_new_entries=30,
        reentry_cooldown_minutes=30,
        no_new_entry_after="15:10",
        force_exit_before_close=True,
        force_exit_time="15:25",
        daily_loss_limit=2_000_000.0,
        stop_loss_pct=0.08,
        trailing_stop_pct=0.10,
        poll_interval_seconds=0.5,
        state_path=str(base),
        log_path=str(log),
        timezone="Asia/Seoul",
        market_open_time="09:00",
        market_close_time="15:30",
        use_mock_stream=True,
        price_rounding_digits=0,
    )


def seed_history(md: MarketDataHandler, symbols: list[str], tz: str, n: int = 90) -> None:
    """신호 워밍업용 과거 분봉을 합성해 ``MarketDataHandler``에 적재한다."""
    import numpy as np

    rng = np.random.default_rng(1)
    base = pd.Timestamp("2026-03-01 09:00", tz=tz)
    for sym in symbols:
        px = 48_000.0 + hash(sym) % 3000
        for i in range(n):
            t = base + pd.Timedelta(minutes=i)
            shock = float(rng.normal(25, 120))
            o = px
            c = max(3000.0, px + shock)
            h = max(o, c) * (1.0 + abs(float(rng.uniform(0, 0.002))))
            l = min(o, c) * (1.0 - abs(float(rng.uniform(0, 0.002))))
            v = float(rng.integers(50_000, 400_000))
            md.update_event(
                MarketEvent(
                    symbol=sym,
                    event_time=t,
                    price=c,
                    volume=v,
                    open=o,
                    high=h,
                    low=l,
                    close=c,
                )
            )
            px = c


def build_components(
    cfg: LiveTradingConfig,
    log: logging.Logger,
) -> tuple[BrokerInterface, LiveTrader]:
    bt_cfg = BacktestConfig(
        max_positions=cfg.max_positions,
        allocation_per_position=cfg.allocation_per_trade,
        stop_loss_pct=cfg.stop_loss_pct,
        trailing_stop_pct=cfg.trailing_stop_pct,
        initial_capital=100_000_000.0,
    )
    signal_model = SignalModel(bt_cfg)
    adapter = SignalAdapter(signal_model, cfg)

    broker = MockBroker(cfg, initial_cash=100_000_000.0, immediate_fill=True, fill_delay_polls=0)
    md = MarketDataHandler(
        max_rows_per_symbol=400,
        merge_ticks_to_minute_bars=cfg.merge_ticks_to_minute_bars,
    )
    seed_history(md, cfg.symbols, cfg.timezone, n=90)
    om = OrderManager(broker, cfg, logger_=log)
    pm = PositionManager()
    rm = RiskManager(cfg, logger_=log)
    store = JSONStateStore(cfg.state_path)

    trader = LiveTrader(
        config=cfg,
        broker=broker,
        signal_adapter=adapter,
        market_data_handler=md,
        order_manager=om,
        position_manager=pm,
        risk_manager=rm,
        state_store=store,
        logger_=log,
    )
    return broker, trader


def main() -> None:
    cfg = build_config()
    log = setup_logging(cfg.log_path)
    Path(cfg.state_path).mkdir(parents=True, exist_ok=True)

    _, trader = build_components(cfg, log)
    log.info("Starting mock live trader demo max_events=300")
    trader.run(max_events=300)

    snap = trader.read_last_snapshot()
    log.info("Snapshot keys: %s", list(snap.keys()))
    positions = trader.list_positions_public()
    print("\n=== Final positions ===")
    for sym, p in positions.items():
        print(sym, p.status.value, "qty=", p.qty, "avg=", round(p.avg_price, 2), "realized=", round(p.realized_pnl, 0))
    print("Cash:", trader.cash_balance())
    print("Events processed:", trader.event_count)


if __name__ == "__main__":
    main()
