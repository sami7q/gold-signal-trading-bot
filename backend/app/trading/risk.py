"""Numeric safety checks used by the rules engine before any order is sent."""

from __future__ import annotations

from decimal import Decimal

from app.integrations.broker.base import SymbolInfo


def is_valid_lot(lot: Decimal | None, symbol_info: SymbolInfo) -> bool:
    if lot is None or lot <= 0:
        return False
    if not (symbol_info.volume_min <= lot <= symbol_info.volume_max):
        return False
    steps = (lot - symbol_info.volume_min) / symbol_info.volume_step
    return steps == steps.to_integral_value()


def is_valid_target_pips(pips: Decimal | None) -> bool:
    return pips is not None and pips > 0


def is_valid_stop_loss_for_buy(stop_loss: Decimal | None, *, reference_price: Decimal) -> bool:
    return stop_loss is not None and stop_loss > 0 and stop_loss < reference_price


def is_valid_take_profit_for_buy(take_profit: Decimal | None, *, reference_price: Decimal) -> bool:
    return take_profit is not None and take_profit > reference_price
