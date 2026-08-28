"""Build the historical channel archive (PROJECT_SPEC.md-style retrospective
analysis, requested explicitly: "pull every signal the channel has ever
posted and analyze win/loss from the beginning").

Not a live-credential script -- it reuses the ALREADY-authorized Telegram
session (see scripts/telegram_login.py / telegram_login_qr.py) and never
prompts for anything, so it's safe to run non-interactively.

What it does, precisely:
  1. Fetches every message the configured channel posted on or after
     `--since` (default 2026-01-01), oldest first.
  2. Replays them through the EXACT SAME `SignalService` the live pipeline
     uses (against a throwaway scratch database, never the real one) --
     guarantees the archive's entry/SL pairing, ambiguity handling, and
     expiry rules are identical to what would have happened live, not a
     second reimplementation that could silently drift from the real logic.
     Expiry is swept incrementally using each message's own real historical
     timestamp (`service.expire_stale_signals(now=data.sent_at)`) *before*
     that message is ingested -- without this, a signal missing a Stop Loss
     never actually flips to EXPIRED mid-replay (only one batch sweep ran,
     at the very end, using today's date), so an arbitrarily late SL
     message could still attach to it even though a live system -- which
     sweeps continuously against wall-clock time -- would already have
     expired that signal and refused the late attachment.
  3. Every signal that reached READY -- classified BUY MARKET, or any other
     side/order-type (SELL, BUY LIMIT, SELL LIMIT) -- gets a simulated
     outcome against real historical MT5 M5 price bars. `classification`
     still distinguishes READY (BUY MARKET -- the only kind this bot
     actually executes live) from NON_EXECUTABLE_TYPE (everything else),
     matching `EXECUTABLE_KINDS` in app/core/constants.py, but both are now
     simulated so the channel's SELL/LIMIT track record can be analyzed
     too:
       - MARKET (BUY/SELL): fills at the next available bar's close after
         the signal completed.
       - LIMIT (BUY_LIMIT/SELL_LIMIT): a pending order at the channel's own
         stated price -- waits (up to 14 days) for a bar that actually
         touches that level; never fabricates a fill the market never
         reached (outcome NEVER_TRIGGERED if it doesn't).
     From the fill, scans forward bar-by-bar for whichever of the channel's
     own Stop Loss or our $10/100-pip Take Profit was crossed first (mirror
     image for SELL: profit on a $10 fall, loss on a rise to the Stop
     Loss). A bar that crosses both in the same 5 minutes is conservatively
     counted as the Stop Loss (we cannot know the intra-bar path, and
     PROJECT_SPEC.md section 32 rule 13 says ambiguity must never default
     to the favorable outcome). If neither is ever crossed in the available
     history, the outcome is UNRESOLVED -- never guessed.
  4. Writes results to the `archived_signals` table (separate from the
     live `signals`/`trades` tables, so resetting live trades never touches
     it) and skips any message_id already archived. Pass `--reset` to wipe
     and fully rebuild this channel's rows instead -- required whenever the
     replay/simulation logic itself changes, since skip-existing would
     otherwise keep old rows computed under the previous (possibly fixed)
     logic forever.

This is an ESTIMATE, not a real trading record: it uses historical Bid-side
M5 OHLC bars with no historical spread applied (fills are computed off the
same price series as SL/TP, unlike live trading where entry uses Ask and
SL/TP execute off Bid) -- documented here rather than hidden.

Usage (from the backend/ directory):
    python scripts/build_channel_archive.py [--since 2026-01-01] [--channel-id ID] [--reset]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from bisect import bisect_left
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402

import app.database.models  # noqa: E402,F401  (populate SQLModel.metadata)
from app.audit.service import AuditService  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.constants import CANONICAL_GOLD_SYMBOL, OrderType, Side, SignalStatus  # noqa: E402
from app.database.models.archived_signal import ArchivedSignal  # noqa: E402
from app.database.models.signal import Signal  # noqa: E402
from app.database.session import get_engine  # noqa: E402
from app.signals.models import IncomingMessageData  # noqa: E402
from app.signals.service import SignalService  # noqa: E402

_CONTRACT_SIZE = Decimal("100")  # matches the real broker's XAUUSD contract size
_TARGET_PIPS = Decimal("100")  # matches the live default (see app/trading/pips.py)
_PIP_SIZE = Decimal("0.1")  # confirmed against the real broker: 1 pip = $0.10
_TP_DISTANCE = _PIP_SIZE * _TARGET_PIPS  # == $10.00


@dataclass(frozen=True, slots=True)
class Bar:
    time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


def _load_price_bars(since: datetime) -> list[Bar]:
    """Historical M5 bars from `since` to now, fetched in monthly chunks
    (a single multi-month request silently returns nothing against this
    broker -- confirmed empirically).

    Uses M5, not M1: confirmed empirically that this broker's M1 history
    only goes back to ~2026-05, while M5 goes back to 2025-03 -- comfortably
    covering the whole archive window with no gap. The requested target is
    still exactly 100 pips / $10 either way; 5-minute resolution only means
    an SL/TP crossing is detected to the nearest 5 minutes instead of 1,
    which never changes which one was crossed first for a signal that
    takes anywhere from minutes to days to resolve.

    This broker's history for a chunk entirely before its real earliest bar
    doesn't come back empty -- `copy_rates_range` returns exactly one bar,
    the single earliest one it has, timestamped outside the requested range
    entirely. Silently accepting that would attribute a random unrelated
    price to every signal before the real cutoff (this was caught by
    exactly that happening with M1: dozens of unrelated signals all showing
    the identical entry price). Any bar outside its own chunk's
    [chunk_start, chunk_end) window is therefore dropped, not trusted.
    """
    import MetaTrader5 as mt5

    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize() failed: {mt5.last_error()}")

    bars: list[Bar] = []
    dropped_out_of_range = 0
    chunk_start = since
    now = datetime.now(UTC)
    while chunk_start < now:
        # Step forward ~30 days per request -- a single multi-month request
        # silently returns nothing against this broker (confirmed).
        chunk_end = min(chunk_start + timedelta(days=30), now)
        rates = mt5.copy_rates_range(
            CANONICAL_GOLD_SYMBOL, mt5.TIMEFRAME_M5, chunk_start, chunk_end
        )
        if rates is not None:
            for r in rates:
                bar_time = datetime.fromtimestamp(int(r["time"]), tz=UTC)
                if not (chunk_start <= bar_time <= chunk_end):
                    dropped_out_of_range += 1
                    continue
                bars.append(
                    Bar(
                        time=bar_time,
                        open=Decimal(str(r["open"])),
                        high=Decimal(str(r["high"])),
                        low=Decimal(str(r["low"])),
                        close=Decimal(str(r["close"])),
                    )
                )
        chunk_start = chunk_end
    mt5.shutdown()
    bars.sort(key=lambda b: b.time)
    print(f"Loaded {len(bars)} historical M5 bars from {since} to {now}.")
    if dropped_out_of_range:
        print(
            f"Dropped {dropped_out_of_range} bar(s) the broker returned outside their "
            "requested chunk window (its real M5 history starts later than `since` --  "
            "signals before that point correctly get NO_PRICE_DATA, never a guessed price)."
        )
    return bars


#: `bisect_left` finds the nearest bar *at or after* the signal's ready
#: time -- with no upper bound, a signal from January would silently be
#: "filled" against a bar from May if that's the earliest data the broker
#: actually has (confirmed: this happened for every pre-May signal before
#: this guard existed). Gold trades ~24/5, so a gap even a few days wide is
#: never a legitimate fill -- only weekends/holidays should ever separate a
#: signal from its nearest bar.
_MAX_FILL_GAP = timedelta(days=3)


#: How long a pending LIMIT order is allowed to wait for price to reach its
#: stated trigger level before we give up and call it NEVER_TRIGGERED. Gold
#: trends for weeks; an unbounded wait would eventually "fill" almost every
#: limit order regardless of whether the channel's setup was ever realistic,
#: which would overstate how often these signals were actually tradeable.
#: 14 days is generous (most channel setups reference nearby levels) while
#: still refusing to guess a fill on a level the market never approached.
_MAX_LIMIT_WAIT = timedelta(days=14)


def _simulate_outcome(
    bars: list[Bar],
    bar_times: list[datetime],
    signal_ready_at: datetime,
    stop_loss: Decimal,
    *,
    side: Side,
    signal_type: OrderType,
    entry_min: Decimal | None,
    entry_max: Decimal | None,
) -> tuple[Decimal, Decimal, str, Decimal | None, datetime | None] | None:
    """Returns (entry_price, take_profit_price, outcome, outcome_price,
    outcome_at), or None if there's no bar reasonably close to/after
    `signal_ready_at` (outside the available historical window ->
    NO_PRICE_DATA, handled by the caller).

    Direction-aware: a SELL profits when price falls $10 and loses when it
    rises to the channel's Stop Loss (the mirror image of BUY). A LIMIT
    order (BUY_LIMIT/SELL_LIMIT) is not filled at signal-ready time like a
    MARKET order -- it is a pending order at the channel's own stated price,
    so this waits (up to `_MAX_LIMIT_WAIT`) for a bar that actually touches
    that level before treating it as filled; if price never gets there the
    outcome is NEVER_TRIGGERED, not a guessed fill.
    """
    start_index = bisect_left(bar_times, signal_ready_at)
    if start_index >= len(bars):
        return None

    ready_bar = bars[start_index]
    if ready_bar.time - signal_ready_at > _MAX_FILL_GAP:
        return None

    if signal_type is OrderType.MARKET:
        entry_price = ready_bar.close
        fill_index = start_index
    else:
        # LIMIT: use whichever bound is nearer the market at signal time --
        # BUY_LIMIT sits below market (price falls to meet it, so the
        # HIGHER of the two stated bounds is touched first); SELL_LIMIT
        # sits above market (the LOWER stated bound is touched first).
        # Falls back to the only bound given when the channel posted a
        # single price rather than a range.
        if side is Side.BUY:
            trigger = entry_max if entry_max is not None else entry_min
        else:
            trigger = entry_min if entry_min is not None else entry_max
        if trigger is None:
            return None

        fill_index = None
        deadline = signal_ready_at + _MAX_LIMIT_WAIT
        for i in range(start_index, len(bars)):
            bar = bars[i]
            if bar.time > deadline:
                break
            touched = bar.low <= trigger <= bar.high
            if touched:
                fill_index = i
                break
        if fill_index is None:
            return (trigger, trigger, "NEVER_TRIGGERED", None, None)
        entry_price = trigger

    take_profit = entry_price + _TP_DISTANCE if side is Side.BUY else entry_price - _TP_DISTANCE

    for bar in bars[fill_index:]:
        if side is Side.BUY:
            hit_sl = bar.low <= stop_loss
            hit_tp = bar.high >= take_profit
        else:
            hit_sl = bar.high >= stop_loss
            hit_tp = bar.low <= take_profit
        if hit_sl and hit_tp:
            return (entry_price, take_profit, "LOSS", stop_loss, bar.time)  # conservative
        if hit_sl:
            return (entry_price, take_profit, "LOSS", stop_loss, bar.time)
        if hit_tp:
            return (entry_price, take_profit, "WIN", take_profit, bar.time)

    return (entry_price, take_profit, "UNRESOLVED", None, None)


async def _fetch_channel_messages(channel_id: int, since: datetime) -> list[IncomingMessageData]:
    from telethon import TelegramClient

    settings = get_settings()
    client = TelegramClient(
        settings.telegram_session_path or "telegram_session",
        int(settings.telegram_api_id),
        settings.telegram_api_hash,
    )
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError(
            "No authorized Telegram session found. Run scripts/telegram_login.py "
            "or scripts/telegram_login_qr.py first."
        )

    messages: list[IncomingMessageData] = []
    async for msg in client.iter_messages(channel_id, offset_date=since, reverse=True):
        if msg.date < since:
            continue
        messages.append(
            IncomingMessageData(
                source="telegram_archive",
                channel_id=str(channel_id),
                message_id=str(msg.id),
                text=msg.raw_text or "",
                sent_at=msg.date,
                received_at=msg.date,
                reply_to_message_id=str(msg.reply_to_msg_id) if msg.reply_to_msg_id else None,
            )
        )
    await client.disconnect()
    print(f"Fetched {len(messages)} messages since {since}.")
    return messages


@dataclass(slots=True)
class ReplayResult:
    signals: list[Signal]
    #: The REAL Telegram message id of the message that *started* each
    #: signal -- used as `ArchivedSignal.message_id` so reruns can dedupe
    #: against actual channel state, not a synthetic/reconstructed id.
    entry_message_id_by_signal_id: dict[int, str]
    entry_text_by_id: dict[int, str]
    sl_text_by_id: dict[int, str]
    #: The REAL historical timestamp (message `sent_at`) of whichever
    #: message actually completed the signal -- `Signal.completed_at` is
    #: NOT usable for this: `SignalService` stamps it with
    #: `datetime.now(UTC)` (correct for live use, but replaying old
    #: messages here happens at today's wall-clock time, which would make
    #: every archived signal look like it completed "just now").
    completed_at_by_id: dict[int, datetime]


def _replay_through_signal_service(messages: list[IncomingMessageData]) -> ReplayResult:
    """Runs the exact live assembly logic against a throwaway in-memory
    scratch database -- never touches the real one."""
    scratch_engine = create_engine("sqlite://")  # in-memory, discarded on exit
    SQLModel.metadata.create_all(scratch_engine)

    entry_message_id_by_signal_id: dict[int, str] = {}
    entry_text_by_id: dict[int, str] = {}
    sl_text_by_id: dict[int, str] = {}
    completed_at_by_id: dict[int, datetime] = {}
    seen_signal_ids: set[int] = set()

    with Session(scratch_engine) as scratch_session:
        service = SignalService(
            scratch_session,
            AuditService(scratch_session),
            signal_completion_timeout_seconds=120,
        )
        for data in messages:
            # CRITICAL: sweep expiry using THIS message's real historical
            # timestamp before ingesting it. Without this, a signal that
            # never got a stop-loss within the 120s completion timeout stays
            # "open" in the scratch DB indefinitely (status is only flipped
            # to EXPIRED by an explicit sweep), so a details message that
            # arrives arbitrarily late -- minutes, hours, even days later --
            # would still attach to it here, even though a live production
            # system (which sweeps continuously against wall-clock time)
            # would have already expired that signal and rejected the late
            # attachment. Confirmed against real data: several archived
            # "READY" signals had a channel-side gap between entry and SL
            # far exceeding 120s (one over 24 minutes) and were still
            # (wrongly) marked READY before this fix.
            service.expire_stale_signals(now=data.sent_at)
            result = service.ingest_message(data)
            if result.signal is None or result.signal.id is None:
                continue
            sid = result.signal.id
            if sid not in seen_signal_ids:
                seen_signal_ids.add(sid)
                entry_message_id_by_signal_id[sid] = data.message_id
                entry_text_by_id[sid] = data.text
            elif result.became_ready:
                sl_text_by_id[sid] = data.text
            if result.became_ready:
                completed_at_by_id[sid] = data.sent_at
        service.expire_stale_signals(now=datetime.now(UTC))

        signals = list(scratch_session.exec(select(Signal)))
        scratch_session.expunge_all()
    return ReplayResult(
        signals=signals,
        entry_message_id_by_signal_id=entry_message_id_by_signal_id,
        entry_text_by_id=entry_text_by_id,
        sl_text_by_id=sl_text_by_id,
        completed_at_by_id=completed_at_by_id,
    )


def build_archive(channel_id: int, since: datetime, *, reset: bool = False) -> None:
    messages = asyncio.run(_fetch_channel_messages(channel_id, since))
    replay = _replay_through_signal_service(messages)
    scratch_signals = replay.signals

    bars = _load_price_bars(since)
    bar_times = [b.time for b in bars]

    engine = get_engine()
    SQLModel.metadata.create_all(engine)  # idempotent -- only creates missing tables
    with Session(engine) as session:
        if reset:
            # Pairing (which SL message attaches to which signal) can change
            # between runs whenever the replay logic itself changes -- e.g.
            # the expiry-timing fix above. Skip-existing-by-message-id would
            # silently keep old, now-wrong rows forever; a full wipe for
            # this channel guarantees every row reflects the current logic.
            existing_rows = list(
                session.exec(
                    select(ArchivedSignal).where(ArchivedSignal.channel_id == str(channel_id))
                )
            )
            for row in existing_rows:
                session.delete(row)
            session.commit()
            print(f"Reset: deleted {len(existing_rows)} existing archived row(s) for this channel.")

        existing_ids = {
            row.message_id
            for row in session.exec(
                select(ArchivedSignal).where(ArchivedSignal.channel_id == str(channel_id))
            )
        }

        counts = {"READY": 0, "NO_SL": 0, "AMBIGUOUS_OR_OTHER": 0, "NON_EXECUTABLE_TYPE": 0}
        outcome_counts = {
            "WIN": 0,
            "LOSS": 0,
            "UNRESOLVED": 0,
            "NO_PRICE_DATA": 0,
            "NEVER_TRIGGERED": 0,
        }
        skipped_duplicate = 0

        for signal in scratch_signals:
            entry_message_id = (
                replay.entry_message_id_by_signal_id.get(signal.id) if signal.id else None
            )
            if entry_message_id is None or entry_message_id in existing_ids:
                if entry_message_id is not None:
                    skipped_duplicate += 1
                continue

            real_completed_at = replay.completed_at_by_id.get(signal.id) if signal.id else None
            # Matches EXECUTABLE_KINDS in app/core/constants.py: only a
            # plain market BUY is ever actually executed live -- BUY LIMIT/
            # STOP and any SELL variant have side=BUY or SELL but are
            # rejected by the rules engine (UNSUPPORTED_SIGNAL_TYPE). The
            # `classification` column still reflects that distinction (so
            # the live dashboard's "what would our bot actually have
            # traded" view is unaffected), but -- per an explicit request to
            # analyze SELL/BUY LIMIT/SELL LIMIT signals too -- both READY
            # and NON_EXECUTABLE_TYPE rows now get a simulated outcome
            # below, not READY alone.
            is_executable_kind = signal.side == Side.BUY and signal.signal_type == OrderType.MARKET

            if signal.status == SignalStatus.READY and is_executable_kind:
                counts["READY"] += 1
            elif signal.status == SignalStatus.READY:
                counts["NON_EXECUTABLE_TYPE"] += 1
            elif signal.status == SignalStatus.EXPIRED:
                counts["NO_SL"] += 1
            else:
                counts["AMBIGUOUS_OR_OTHER"] += 1

            row = ArchivedSignal(
                channel_id=str(channel_id),
                message_id=entry_message_id,
                symbol=signal.symbol,
                side=signal.side.value,
                signal_type=signal.signal_type.value,
                entry_min=signal.entry_min,
                entry_max=signal.entry_max,
                channel_stop_loss=signal.channel_stop_loss,
                raw_entry_text=replay.entry_text_by_id.get(signal.id, ""),
                raw_sl_text=replay.sl_text_by_id.get(signal.id),
                signal_posted_at=signal.first_message_at,
                sl_posted_at=real_completed_at,
                classification=(
                    "READY"
                    if signal.status == SignalStatus.READY and is_executable_kind
                    else "NON_EXECUTABLE_TYPE"
                    if signal.status == SignalStatus.READY
                    else "NO_SL"
                    if signal.status == SignalStatus.EXPIRED
                    else "AMBIGUOUS_OR_OTHER"
                ),
            )

            if (
                row.classification in ("READY", "NON_EXECUTABLE_TYPE")
                and signal.channel_stop_loss is not None
                and real_completed_at is not None
            ):
                result = _simulate_outcome(
                    bars,
                    bar_times,
                    real_completed_at,
                    signal.channel_stop_loss,
                    side=signal.side,
                    signal_type=signal.signal_type,
                    entry_min=signal.entry_min,
                    entry_max=signal.entry_max,
                )
                if result is None:
                    row.outcome = "NO_PRICE_DATA"
                    outcome_counts["NO_PRICE_DATA"] += 1
                else:
                    entry_price, tp_price, outcome, outcome_price, outcome_at = result
                    row.simulated_entry_price = entry_price
                    row.simulated_take_profit_price = tp_price
                    row.outcome = outcome
                    row.outcome_price = outcome_price
                    row.outcome_at = outcome_at
                    if outcome_price is not None:
                        signed = 1 if signal.side == Side.BUY else -1
                        row.profit_estimate = (
                            (outcome_price - entry_price) * _CONTRACT_SIZE * signed
                        )
                    outcome_counts[outcome] += 1

            session.add(row)
            existing_ids.add(entry_message_id)

        session.commit()

    print(f"\nSkipped (already archived): {skipped_duplicate}")
    print(f"Classification counts: {counts}")
    print(f"Outcome counts (READY + NON_EXECUTABLE_TYPE, all sides/types): {outcome_counts}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default="2026-01-01", help="YYYY-MM-DD, default 2026-01-01")
    parser.add_argument(
        "--channel-id", type=int, default=None, help="Defaults to TELEGRAM_ALLOWED_CHANNEL_ID"
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete all existing archived rows for this channel before rebuilding.",
    )
    args = parser.parse_args()

    settings = get_settings()
    channel_id = args.channel_id or int(settings.telegram_allowed_channel_id)
    since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=UTC)

    print(f"Building archive for channel {channel_id} since {since.date()}...")
    build_archive(channel_id, since, reset=args.reset)
    print("\nDone.")


if __name__ == "__main__":
    main()
