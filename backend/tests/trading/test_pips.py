"""`compute_stop_loss` -- the mirror of `compute_take_profit`, added to back
the auto Stop Loss fallback (TradingSettings.auto_sl_fallback_pips,
app/trading/service.py::TradingService._apply_auto_stop_loss_fallback)."""

from __future__ import annotations

from decimal import Decimal

from app.core.constants import CANONICAL_GOLD_SYMBOL, Side
from app.integrations.broker.base import SymbolInfo
from app.trading import pips


def _symbol_info() -> SymbolInfo:
    return SymbolInfo(
        symbol=CANONICAL_GOLD_SYMBOL,
        digits=2,
        point=Decimal("0.01"),
        contract_size=Decimal("100"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("50"),
        volume_step=Decimal("0.01"),
    )


def test_compute_stop_loss_for_buy_is_behind_entry():
    # pip_size for 2-digit gold is point*10 = 0.1, so 150 pips = 15.00.
    result = pips.compute_stop_loss(
        entry_price=Decimal("4630.00"),
        pips=Decimal("150"),
        symbol_info=_symbol_info(),
        side=Side.BUY,
    )
    assert result == Decimal("4615.00")


def test_compute_stop_loss_for_sell_is_ahead_of_entry():
    result = pips.compute_stop_loss(
        entry_price=Decimal("4630.00"),
        pips=Decimal("150"),
        symbol_info=_symbol_info(),
        side=Side.SELL,
    )
    assert result == Decimal("4645.00")


def test_compute_stop_loss_defaults_to_buy_side():
    result = pips.compute_stop_loss(
        entry_price=Decimal("4630.00"), pips=Decimal("150"), symbol_info=_symbol_info()
    )
    assert result == Decimal("4615.00")


def test_compute_stop_loss_is_the_mirror_of_compute_take_profit():
    entry = Decimal("4630.00")
    symbol_info = _symbol_info()
    sl = pips.compute_stop_loss(
        entry_price=entry, pips=Decimal("150"), symbol_info=symbol_info, side=Side.BUY
    )
    tp = pips.compute_take_profit(
        entry_price=entry, pips=Decimal("150"), symbol_info=symbol_info, side=Side.BUY
    )
    # Same distance from entry, opposite directions.
    assert entry - sl == tp - entry
