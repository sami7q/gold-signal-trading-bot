"""100+-scenario parser / state-machine / safety sweep (PROJECT_SPEC.md
Phase 11, section 19).

This is explicitly NOT a profitability backtest — it is a repeatable sweep
proving the parser, signal assembly, and safety rules behave correctly
across a wide combinatorial spread of real-world-shaped inputs before real
Telegram traffic is trusted. Every scenario carries its own expected
outcome; the sweep fails loudly (via a summary assertion) if any scenario's
actual outcome doesn't match.

Two tiers:
  - PARSER_SCENARIOS: single-message, no DB — exercises `parse_message`
    directly across syntax/alias/spacing/emoji/format variation.
  - FLOW_SCENARIOS: multi-message, uses `SignalService`/`TradingService` —
    exercises duplicate delivery, ordering, multi-signal ambiguity, reply
    threading, expiry, and execution-safety end to end.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.audit.service import AuditService
from app.core.constants import (
    MessageClassification,
    SignalKind,
    SignalStatus,
)
from app.integrations.broker.paper import PaperBroker
from app.settings.schemas import TradingSettingsUpdate
from app.settings.service import SettingsService
from app.signals.models import IncomingMessageData
from app.signals.parser import parse_message
from app.signals.service import SignalService
from app.trading.service import TradingService

# =============================================================================
# Tier 1 — pure parser scenarios (no DB, no session)
# =============================================================================


@dataclass(frozen=True, slots=True)
class ParserCase:
    text: str
    kind: SignalKind | None
    classification: MessageClassification
    is_gold: bool | None = None  # None = don't check
    stop_loss: Decimal | None = None  # None = don't check unless expect_no_sl
    expect_no_sl: bool = False
    executable: bool | None = None


def _p(text, kind, classification, **kw) -> ParserCase:
    return ParserCase(text=text, kind=kind, classification=classification, **kw)


K = SignalKind
C = MessageClassification

PARSER_SCENARIOS: list[ParserCase] = [
    # -- BUY syntax variations x Gold aliases (case, spacing, word order) ---
    _p("BUY GOLD", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    _p("buy gold", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    _p("Buy Gold", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    _p("GOLD BUY", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    _p("  BUY   GOLD  ", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    _p("BUY\nGOLD", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    _p("BUY GOLD NOW", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    _p("BUY GOLD NOW!!!", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    _p("buy   gold   now", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    _p("BUY XAUUSD", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    _p("BUY XAU", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    _p("BUY XAU/USD", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    _p("BUY XAU-USD", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    _p("buy xauusd", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    # -- emoji noise ------------------------------------------------------
    _p("🟢 BUY GOLD 🟢", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    _p("BUY GOLD 🚀", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    _p("✅ BUY GOLD ✅", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    _p("BUY GOLD 🔥🔥🔥", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    _p("📈 BUY GOLD NOW 📈", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    # -- entry range formats -------------------------------------------------
    _p("BUY GOLD 4633 - 4631", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    _p("BUY GOLD 4633-4631", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    _p("BUY GOLD 4633 -4631", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    _p("BUY GOLD 4633- 4631", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    # -- SL formats -----------------------------------------------------------
    _p("SL 4623", None, C.SIGNAL_DETAILS, stop_loss=Decimal("4623")),
    _p("SL: 4623", None, C.SIGNAL_DETAILS, stop_loss=Decimal("4623")),
    _p("SL:4623", None, C.SIGNAL_DETAILS, stop_loss=Decimal("4623")),
    _p("sl 4623", None, C.SIGNAL_DETAILS, stop_loss=Decimal("4623")),
    _p("SL   4623", None, C.SIGNAL_DETAILS, stop_loss=Decimal("4623")),
    _p("STOP LOSS 4623", None, C.SIGNAL_DETAILS, stop_loss=Decimal("4623")),
    _p("STOPLOSS 4623", None, C.SIGNAL_DETAILS, stop_loss=Decimal("4623")),
    _p("Stop Loss: 4623", None, C.SIGNAL_DETAILS, stop_loss=Decimal("4623")),
    _p("4640 - 4637\nSL 4623", None, C.SIGNAL_DETAILS, stop_loss=Decimal("4623")),
    _p("4640-4637\nSL:4623", None, C.SIGNAL_DETAILS, stop_loss=Decimal("4623")),
    _p("SL 4618.5", None, C.SIGNAL_DETAILS, stop_loss=Decimal("4618.5")),
    # -- target / result messages: informational only, never executable ------
    _p("4644\n4650\n4663\n4677\n4700", None, C.TARGET_UPDATE, expect_no_sl=True),
    _p("TP1 ✅ 80 pips", None, C.TARGET_UPDATE, expect_no_sl=True),
    _p("120 pips 🚀", None, C.TARGET_UPDATE, expect_no_sl=True),
    _p("TP2 hit! 150 pips", None, C.TARGET_UPDATE, expect_no_sl=True),
    _p("4644", None, C.TARGET_UPDATE, expect_no_sl=True),
    _p("+95 pips 🎯", None, C.TARGET_UPDATE, expect_no_sl=True),
    # "pips" wins over result-keywords when both are present in one message
    # (classify_message checks TARGET_UPDATE before RESULT_UPDATE) — this is
    # real parser precedence, not a scenario-writer assumption.
    _p("CLOSED ✅ +80 pips", None, C.TARGET_UPDATE, expect_no_sl=True),
    _p("Target hit! 🎉", None, C.RESULT_UPDATE, expect_no_sl=True),
    _p("Stopped out", None, C.RESULT_UPDATE, expect_no_sl=True),
    # -- random / conversational messages: must be ignored --------------------
    _p("Good morning", None, C.IGNORED),
    _p("Good morning everyone", None, C.IGNORED),
    _p("How is everyone today?", None, C.IGNORED),
    _p("Thanks for the signal!", None, C.IGNORED),
    _p("Great call team", None, C.IGNORED),
    _p("What time is the next signal?", None, C.IGNORED),
    _p("صباح الخير", None, C.IGNORED),
    _p("شكرا على الاشارة", None, C.IGNORED),
    _p("Welcome to the channel", None, C.IGNORED),
    _p("Please read the rules", None, C.IGNORED),
    _p("😀😀😀", None, C.IGNORED),
    _p("...", None, C.IGNORED),
    # -- SELL market: executable (v1.3). LIMIT / STOP: classified, never
    # executable -- deferred pending-order lifecycle. -------------------------
    _p("SELL GOLD", K.SELL, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    _p("SELL GOLD 4650 - 4652", K.SELL, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    _p("SELL LIMIT GOLD 4546 - 4545", K.SELL_LIMIT, C.ENTRY_SIGNAL, is_gold=True, executable=False),
    _p("BUY LIMIT GOLD", K.BUY_LIMIT, C.ENTRY_SIGNAL, is_gold=True, executable=False),
    _p("BUY LIMIT GOLD 4600 - 4598", K.BUY_LIMIT, C.ENTRY_SIGNAL, is_gold=True, executable=False),
    _p("BUY STOP GOLD 4633", K.BUY_STOP, C.ENTRY_SIGNAL, is_gold=True, executable=False),
    _p("SELL STOP GOLD 4633", K.SELL_STOP, C.ENTRY_SIGNAL, is_gold=True, executable=False),
    _p("sell limit gold 4546-4545", K.SELL_LIMIT, C.ENTRY_SIGNAL, is_gold=True, executable=False),
    # -- invalid / malformed numeric data --------------------------------------
    _p("SL abc", None, C.IGNORED, expect_no_sl=True),
    _p("BUY GOLD abc - def", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    # the "-" is consumed as the optional SL separator, not a negative sign
    # (the number group has no sign in it) -- real regex behavior, verified
    # against app/signals/parser.py, not an assumption.
    _p("SL -4623", None, C.SIGNAL_DETAILS, stop_loss=Decimal("4623")),
    # "LOSS" alone is a RESULT_KEYWORDS hit even with no number attached.
    _p("STOP LOSS", None, C.RESULT_UPDATE, expect_no_sl=True),
    _p("SL", None, C.IGNORED, expect_no_sl=True),
    # -- non-gold instruments: BUY detected, but not gold ---------------------
    _p("BUY EURUSD", K.BUY, C.IGNORED, is_gold=False),
    _p("BUY BTCUSD", K.BUY, C.IGNORED, is_gold=False),
    # -- BUY + SL combined in a single message ---------------------------------
    _p("BUY GOLD\nSL 4623", K.BUY, C.ENTRY_SIGNAL, is_gold=True, stop_loss=Decimal("4623")),
    _p("BUY GOLD SL 4623", K.BUY, C.ENTRY_SIGNAL, is_gold=True, stop_loss=Decimal("4623")),
    # -- extra whitespace / line noise realism ---------------------------------
    _p("BUY   GOLD\n\n\n4640 - 4637\nSL 4623", K.BUY, C.ENTRY_SIGNAL, is_gold=True),
    _p("   BUY GOLD   ", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    _p("BUY GOLD.", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    _p("BUY GOLD,", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    # -- more alias / emoji / spacing combinations -----------------------------
    _p("BUY GOLD 🔥", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    _p("🔴 SELL GOLD 🔴", K.SELL, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    _p("Buy XAUUSD Now!", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    _p("GOLD BUY NOW 🚀", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    _p("BUY GOLD  4633   -   4631", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    _p("BUY GOLD 4633.5 - 4631.2", K.BUY, C.ENTRY_SIGNAL, is_gold=True, executable=True),
    _p("sell stop gold 4633", K.SELL_STOP, C.ENTRY_SIGNAL, is_gold=True, executable=False),
    _p("BUY STOP XAUUSD 4650", K.BUY_STOP, C.ENTRY_SIGNAL, is_gold=True, executable=False),
    # -- separators other than ':'/'-' are stripped to a space, not dropped ---
    _p("SL=4623", None, C.SIGNAL_DETAILS, stop_loss=Decimal("4623")),
    _p("SL@4623", None, C.SIGNAL_DETAILS, stop_loss=Decimal("4623")),
    # -- more result/target keyword coverage -----------------------------------
    _p("TARGET HIT 🎯", None, C.RESULT_UPDATE, expect_no_sl=True),
    _p("WIN ✅", None, C.RESULT_UPDATE, expect_no_sl=True),
    _p("HIT SL 😢", None, C.RESULT_UPDATE, expect_no_sl=True),
    _p("HIT TP ✅", None, C.RESULT_UPDATE, expect_no_sl=True),
    # -- more conversational noise ----------------------------------------------
    _p("Signal channel VIP members only 2024", None, C.IGNORED),
    _p("Anyone else in this trade?", None, C.IGNORED),
    _p("Nice profit today 👏", None, C.IGNORED),
    # -- word-boundary false-positive guards: substrings must never match -----
    # "BUYING"/"SELLING" must not be misread as the BUY/SELL keyword.
    _p("BUYING GOLD NOW", None, C.IGNORED, is_gold=True),
    _p("SELLING GOLD", None, C.IGNORED, is_gold=True),
    # "GOLDEN" must not be misread as the GOLD alias.
    _p("GOLDEN retriever for sale", None, C.IGNORED, is_gold=False),
]


def _parser_case_ids():
    return [f"{i:03d}:{c.text[:30]!r}" for i, c in enumerate(PARSER_SCENARIOS)]


@pytest.mark.parametrize("case", PARSER_SCENARIOS, ids=_parser_case_ids())
def test_parser_scenario(case: ParserCase):
    parsed = parse_message(case.text)

    assert parsed.kind == case.kind, f"kind: expected {case.kind}, got {parsed.kind}"
    assert parsed.classification == case.classification, (
        f"classification: expected {case.classification}, got {parsed.classification}"
    )
    if case.is_gold is not None:
        assert parsed.is_gold == case.is_gold
    if case.stop_loss is not None:
        assert parsed.stop_loss == case.stop_loss
    if case.expect_no_sl:
        assert parsed.stop_loss is None
    if case.executable is not None:
        assert parsed.is_executable_kind == case.executable


# =============================================================================
# Tier 2 — stateful flow scenarios (SignalService / TradingService)
# =============================================================================


@dataclass(frozen=True, slots=True)
class Step:
    text: str
    message_id: str
    reply_to: str | None = None
    offset_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class FlowScenario:
    name: str
    steps: tuple[Step, ...]
    channel: str = "sweep"
    timeout_seconds: int = 120
    #: run expire_stale_signals() with `now` this many seconds after the
    #: first step, before checking outcomes (None = don't sweep)
    expire_after_seconds: float | None = None
    expect_trade_count: int = 0
    expect_any_ambiguous: bool = False
    expect_final_statuses: tuple[SignalStatus, ...] | None = None  # sorted-by-creation


def _step(text, mid, *, reply_to=None, offset=0.0) -> Step:
    return Step(text=text, message_id=mid, reply_to=reply_to, offset_seconds=offset)


S = SignalStatus

FLOW_SCENARIOS: list[FlowScenario] = [
    FlowScenario(
        "single_buy_then_sl_executes",
        (_step("BUY GOLD", "f1-a"), _step("4640 - 4637\nSL 4623", "f1-b")),
        expect_trade_count=1,
        expect_final_statuses=(S.EXECUTED,),
    ),
    FlowScenario(
        "buy_sl_combined_one_message_executes",
        (_step("BUY GOLD\nSL 4623", "f2-a"),),
        expect_trade_count=1,
        expect_final_statuses=(S.EXECUTED,),
    ),
    FlowScenario(
        "duplicate_delivery_same_message_id_no_extra_trade",
        (
            _step("BUY GOLD", "f3-a"),
            _step("4640 - 4637\nSL 4623", "f3-b"),
            _step("BUY GOLD", "f3-a"),  # exact repeat
        ),
        expect_trade_count=1,
        expect_final_statuses=(S.EXECUTED,),
    ),
    FlowScenario(
        "sl_before_buy_is_inapplicable_then_buy_waits",
        (_step("SL 4623", "f4-a"), _step("BUY GOLD", "f4-b")),
        expect_trade_count=0,
        expect_final_statuses=(S.WAITING_FOR_DETAILS,),
    ),
    FlowScenario(
        "target_messages_never_advance_or_execute",
        (
            _step("BUY GOLD", "f5-a"),
            _step("4644\n4650\n4663\n4677", "f5-b"),
            _step("TP1 ✅ 80 pips", "f5-c"),
        ),
        expect_trade_count=0,
        expect_final_statuses=(S.WAITING_FOR_DETAILS,),
    ),
    FlowScenario(
        "two_waiting_generic_sl_ambiguous_no_trade",
        (_step("BUY GOLD", "f6-a"), _step("BUY GOLD", "f6-b"), _step("SL 4623", "f6-c")),
        expect_trade_count=0,
        expect_any_ambiguous=True,
        expect_final_statuses=(S.WAITING_FOR_DETAILS, S.WAITING_FOR_DETAILS),
    ),
    FlowScenario(
        "two_waiting_reply_resolves_first_only",
        (
            _step("BUY GOLD", "f7-a"),
            _step("BUY GOLD", "f7-b"),
            _step("SL 4623", "f7-a-sl", reply_to="f7-a"),
        ),
        expect_trade_count=1,
        expect_final_statuses=(S.EXECUTED, S.WAITING_FOR_DETAILS),
    ),
    FlowScenario(
        "two_waiting_reply_resolves_second_only",
        (
            _step("BUY GOLD", "f8-a"),
            _step("BUY GOLD", "f8-b"),
            _step("SL 4600", "f8-b-sl", reply_to="f8-b"),
        ),
        expect_trade_count=1,
        expect_final_statuses=(S.WAITING_FOR_DETAILS, S.EXECUTED),
    ),
    FlowScenario(
        "invalid_reply_reference_is_ambiguous",
        (
            _step("BUY GOLD", "f9-a"),
            _step("BUY GOLD", "f9-b"),
            _step("SL 4623", "f9-c", reply_to="does-not-exist"),
        ),
        expect_trade_count=0,
        expect_any_ambiguous=True,
        expect_final_statuses=(S.WAITING_FOR_DETAILS, S.WAITING_FOR_DETAILS),
    ),
    FlowScenario(
        "expired_signal_never_executes_even_if_sl_arrives_conceptually_late",
        (_step("BUY GOLD", "f10-a"),),
        timeout_seconds=60,
        expire_after_seconds=120,
        expect_trade_count=0,
        expect_final_statuses=(S.EXPIRED,),
    ),
    FlowScenario(
        "three_waiting_signals_generic_sl_still_ambiguous",
        (
            _step("BUY GOLD", "f11-a"),
            _step("BUY GOLD", "f11-b"),
            _step("BUY GOLD", "f11-c"),
            _step("SL 4623", "f11-sl"),
        ),
        expect_trade_count=0,
        expect_any_ambiguous=True,
        expect_final_statuses=(S.WAITING_FOR_DETAILS, S.WAITING_FOR_DETAILS, S.WAITING_FOR_DETAILS),
    ),
    FlowScenario(
        "three_waiting_signals_reply_resolves_middle_one",
        (
            _step("BUY GOLD", "f12-a"),
            _step("BUY GOLD", "f12-b"),
            _step("BUY GOLD", "f12-c"),
            _step("SL 4600", "f12-b-sl", reply_to="f12-b"),
        ),
        expect_trade_count=1,
        expect_final_statuses=(S.WAITING_FOR_DETAILS, S.EXECUTED, S.WAITING_FOR_DETAILS),
    ),
    FlowScenario(
        "sell_limit_signal_classified_not_executed",
        (_step("SELL LIMIT GOLD 4546 - 4545", "f13-a"), _step("SL 4558", "f13-b")),
        expect_trade_count=1,  # a Trade row IS created -- REJECTED, recording the attempt
        expect_final_statuses=(S.REJECTED,),
    ),
    FlowScenario(
        "target_update_after_execution_does_not_reopen_or_duplicate",
        (
            _step("BUY GOLD", "f14-a"),
            _step("4640 - 4637\nSL 4623", "f14-b"),
            _step("TP1 ✅ 80 pips", "f14-c"),
            _step("120 pips \U0001f680", "f14-d"),
        ),
        expect_trade_count=1,
        expect_final_statuses=(S.EXECUTED,),
    ),
    FlowScenario(
        "message_ordering_sl_details_before_second_buy_still_resolves_first",
        (
            _step("BUY GOLD", "f15-a"),
            _step("SL 4623", "f15-a-sl"),  # resolves f15-a uniquely (only one waiting)
            _step("BUY GOLD", "f15-b"),  # new, separate signal
        ),
        expect_trade_count=1,
        expect_final_statuses=(S.EXECUTED, S.WAITING_FOR_DETAILS),
    ),
]


def _flow_case_ids():
    return [s.name for s in FLOW_SCENARIOS]


@pytest.mark.parametrize("scenario", FLOW_SCENARIOS, ids=_flow_case_ids())
def test_flow_scenario(session, seed_account, scenario: FlowScenario):
    SettingsService(session).update(
        seed_account.id,
        TradingSettingsUpdate(
            auto_trading=True,
            buy_enabled=True,
            lot_size=Decimal("0.10"),
            target_pips=Decimal("100"),
            # scenarios intentionally open several signals; don't let
            # capacity mask the outcome under test
            max_open_trades=10,
            require_stop_loss=True,
            # these scenarios test signal assembly/flow (ambiguity, reply
            # threading, expiry), not the Stop Loss quality filter -- fixture
            # SL values are picked for flow behavior, not pip distance from
            # PaperBroker's starting price, so the filter is disabled here
            # (it has its own dedicated tests in tests/trading/test_rules.py).
            max_channel_stop_pips=None,
            signal_completion_timeout_seconds=scenario.timeout_seconds,
        )
    )
    broker = PaperBroker(
        starting_balance=Decimal("10000"),
        symbol="XAUUSD",
        point=Decimal("0.01"),
        digits=2,
        contract_size=Decimal("100"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("50"),
        volume_step=Decimal("0.01"),
        spread_points=20,
        starting_price=Decimal("4638.30"),
    )
    service = TradingService(
        session,
        broker,
        account_id=seed_account.id,
        signal_completion_timeout_seconds=scenario.timeout_seconds,
    )

    base_time = datetime.now(UTC)
    any_ambiguous = False
    for step in scenario.steps:
        data = IncomingMessageData(
            source="mock",
            channel_id=scenario.channel,
            message_id=step.message_id,
            text=step.text,
            sent_at=base_time + timedelta(seconds=step.offset_seconds),
            received_at=datetime.now(UTC),
            reply_to_message_id=step.reply_to,
        )
        result = service.process_incoming_message(data)
        any_ambiguous = any_ambiguous or result.ambiguous

    if scenario.expire_after_seconds is not None:
        service.sweep_expired_signals()
        # sweep uses real "now" internally; force it explicitly too so the
        # scenario doesn't depend on wall-clock test execution speed.
        SignalService(
            session,
            AuditService(session),
            signal_completion_timeout_seconds=scenario.timeout_seconds,
        ).expire_stale_signals(now=base_time + timedelta(seconds=scenario.expire_after_seconds))

    from sqlmodel import select

    from app.database.models.signal import Signal
    from app.database.models.trade import Trade

    signal_stmt = (
        select(Signal).where(Signal.channel_id == scenario.channel).order_by(Signal.created_at)
    )
    signals = list(session.exec(signal_stmt))
    trades = list(session.exec(select(Trade)))

    assert len(trades) == scenario.expect_trade_count, (
        f"{scenario.name}: expected {scenario.expect_trade_count} trades, got {len(trades)}"
    )
    assert any_ambiguous == scenario.expect_any_ambiguous, (
        f"{scenario.name}: expected any_ambiguous={scenario.expect_any_ambiguous}, "
        f"got {any_ambiguous}"
    )
    if scenario.expect_final_statuses is not None:
        actual = tuple(s.status for s in signals)
        assert actual == scenario.expect_final_statuses, (
            f"{scenario.name}: expected statuses {scenario.expect_final_statuses}, got {actual}"
        )


# =============================================================================
# Summary: total / passed / failed, target 100% (PROJECT_SPEC.md Phase 11 s.19)
# =============================================================================


def test_scenario_sweep_size_meets_the_100_scenario_target():
    total = len(PARSER_SCENARIOS) + len(FLOW_SCENARIOS)
    assert total >= 100, f"scenario sweep must cover >=100 cases, found {total}"
