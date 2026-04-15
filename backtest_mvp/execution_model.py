"""Execution pricing: fees, tax, slippage. No signal logic."""

from __future__ import annotations

from dataclasses import dataclass

from config import BacktestConfig


@dataclass(frozen=True)
class BuyFill:
    price: float
    quantity: int
    gross: float
    commission: float
    tax: float


@dataclass(frozen=True)
class SellFill:
    price: float
    quantity: int
    gross: float
    commission: float
    tax: float


def buy_at_open(open_price: float, allocation: float, cfg: BacktestConfig) -> BuyFill | None:
    """Buy at signal_idx+1 open: price = open * (1 + slippage)."""
    if open_price <= 0 or allocation <= 0:
        return None
    exec_price = open_price * (1.0 + cfg.slippage_pct)
    qty = int(allocation // exec_price)
    if qty <= 0:
        return None
    gross = exec_price * qty
    commission = gross * cfg.commission_rate
    tax = 0.0
    return BuyFill(
        price=exec_price,
        quantity=qty,
        gross=gross,
        commission=commission,
        tax=tax,
    )


def sell_normal_at_close(close_price: float, quantity: int, cfg: BacktestConfig) -> SellFill | None:
    """일반 청산: close 기준 (seller receives less due to slippage)."""
    if close_price <= 0 or quantity <= 0:
        return None
    exec_price = close_price * (1.0 - cfg.slippage_pct)
    gross = exec_price * quantity
    commission = gross * cfg.commission_rate
    tax = gross * cfg.tax_rate
    return SellFill(
        price=exec_price,
        quantity=quantity,
        gross=gross,
        commission=commission,
        tax=tax,
    )


def sell_stop_loss(
    open_price: float,
    low_price: float,
    stop_price: float,
    quantity: int,
    cfg: BacktestConfig,
) -> SellFill | None:
    """
    손절: 저가 기준, 갭하락 포함.
    - open <= stop → 시가 손절
    - low <= stop → stop 가격 손절 (갭으로 이미 stop 아래면 시가가 우선)
    """
    if quantity <= 0 or stop_price <= 0:
        return None
    if open_price <= stop_price:
        exec_price = open_price * (1.0 - cfg.slippage_pct)
    elif low_price <= stop_price:
        exec_price = stop_price * (1.0 - cfg.slippage_pct)
    else:
        return None
    gross = exec_price * quantity
    commission = gross * cfg.commission_rate
    tax = gross * cfg.tax_rate
    return SellFill(
        price=exec_price,
        quantity=quantity,
        gross=gross,
        commission=commission,
        tax=tax,
    )


def sell_at_open(open_price: float, quantity: int, cfg: BacktestConfig) -> SellFill | None:
    """Signal-based exit at next session open (e.g. MA20 break)."""
    if open_price <= 0 or quantity <= 0:
        return None
    exec_price = open_price * (1.0 - cfg.slippage_pct)
    gross = exec_price * quantity
    commission = gross * cfg.commission_rate
    tax = gross * cfg.tax_rate
    return SellFill(
        price=exec_price,
        quantity=quantity,
        gross=gross,
        commission=commission,
        tax=tax,
    )
