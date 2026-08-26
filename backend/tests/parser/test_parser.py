"""Parser fixtures from PROJECT_SPEC.md section 17.1."""

from __future__ import annotations

from decimal import Decimal

from app.core.constants import CANONICAL_GOLD_SYMBOL, MessageClassification, SignalKind
from app.signals.parser import normalize_instrument, parse_message


def test_buy_gold_detected_as_buy_xauusd():
    parsed = parse_message("BUY GOLD")
    assert parsed.kind == SignalKind.BUY
    assert parsed.is_gold is True
    assert normalize_instrument("GOLD") == CANONICAL_GOLD_SYMBOL
    assert parsed.classification == MessageClassification.ENTRY_SIGNAL


def test_buy_xauusd_detected():
    parsed = parse_message("BUY XAUUSD")
    assert parsed.kind == SignalKind.BUY
    assert parsed.is_gold is True


def test_buy_gold_with_entry_range():
    parsed = parse_message("BUY GOLD 4633 - 4631")
    assert parsed.kind == SignalKind.BUY
    assert parsed.entry_min == Decimal("4631")
    assert parsed.entry_max == Decimal("4633")


def test_stop_loss_extraction():
    parsed = parse_message("SL 4618")
    assert parsed.stop_loss == Decimal("4618")


def test_stop_loss_with_colon():
    parsed = parse_message("SL: 4623")
    assert parsed.stop_loss == Decimal("4623")


def test_stop_loss_verbose_forms():
    assert parse_message("STOP LOSS 4623").stop_loss == Decimal("4623")
    assert parse_message("STOPLOSS 4623").stop_loss == Decimal("4623")


def test_tp_update_is_informational_only():
    parsed = parse_message("TP1 ✅ 80 pips")
    assert parsed.classification == MessageClassification.TARGET_UPDATE
    assert parsed.kind is None


def test_bare_pips_update_is_informational_only():
    parsed = parse_message("120 pips \U0001f680")
    assert parsed.classification == MessageClassification.TARGET_UPDATE
    assert parsed.kind is None


def test_greeting_is_ignored():
    parsed = parse_message("Good morning")
    assert parsed.classification == MessageClassification.IGNORED
    assert parsed.kind is None


def test_sell_limit_classified_but_not_buy_executable():
    parsed = parse_message("SELL LIMIT GOLD 4546 - 4545")
    assert parsed.kind == SignalKind.SELL_LIMIT
    assert parsed.classification == MessageClassification.ENTRY_SIGNAL
    assert parsed.is_executable_kind is False


def test_multi_message_details_only_has_entry_and_sl():
    parsed = parse_message("4640 - 4637\nSL 4623")
    assert parsed.kind is None
    assert parsed.entry_min == Decimal("4637")
    assert parsed.entry_max == Decimal("4640")
    assert parsed.stop_loss == Decimal("4623")
    assert parsed.classification == MessageClassification.SIGNAL_DETAILS


def test_target_list_stored_as_informational_only():
    parsed = parse_message("4644\n4650\n4663\n4677\n4700")
    assert parsed.kind is None
    assert parsed.classification == MessageClassification.TARGET_UPDATE
    assert parsed.target_prices == [
        Decimal("4644"),
        Decimal("4650"),
        Decimal("4663"),
        Decimal("4677"),
        Decimal("4700"),
    ]


def test_buy_limit_and_stop_variants_detected():
    assert parse_message("BUY LIMIT GOLD 4633 - 4631").kind == SignalKind.BUY_LIMIT
    assert parse_message("BUY STOP GOLD 4633").kind == SignalKind.BUY_STOP
    assert parse_message("SELL STOP GOLD 4633").kind == SignalKind.SELL_STOP
    assert parse_message("SELL GOLD 4633").kind == SignalKind.SELL
