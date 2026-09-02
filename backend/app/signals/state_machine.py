"""Explicit signal state machine (PROJECT_SPEC.md section 7).

Pure, DB-free transition logic so it can be unit tested in isolation.
`app/signals/service.py` is the only caller that persists the result.

States::

    RECEIVED -> CLASSIFIED -> WAITING_FOR_DETAILS -> READY -> VALIDATING
                                                    -> EXPIRED
    (any non-terminal) -> DUPLICATE   [handled before parsing, see service.py]
    READY -> REJECTED                 [rules engine, see app/trading/rules.py]
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.core.constants import SignalStatus

#: Statuses from which the signal can still progress toward READY.
OPEN_STATUSES: frozenset[SignalStatus] = frozenset(
    {SignalStatus.RECEIVED, SignalStatus.CLASSIFIED, SignalStatus.WAITING_FOR_DETAILS}
)

TERMINAL_STATUSES: frozenset[SignalStatus] = frozenset(
    {
        SignalStatus.REJECTED,
        SignalStatus.EXECUTED,
        SignalStatus.FAILED,
        SignalStatus.EXPIRED,
        SignalStatus.IGNORED,
        SignalStatus.DUPLICATE,
    }
)

#: Every status a signal reaches once the state machine has actually
#: decided something about it -- has (or once had) a real Stop Loss and a
#: side/symbol worth executing against, as opposed to still being
#: assembled (`OPEN_STATUSES`) or having been ruled out entirely
#: (EXPIRED/IGNORED/DUPLICATE: never had -- or will never get -- a Stop
#: Loss, or was never a real signal to begin with). Used by
#: `SignalService.find_unexecuted_for_account` (concurrent-execution
#: "Phase 2" update) to decide which signals a per-account worker should
#: even consider -- trying to execute an EXPIRED/IGNORED/DUPLICATE signal
#: makes no sense regardless of account.
EXECUTION_ELIGIBLE_STATUSES: frozenset[SignalStatus] = TERMINAL_STATUSES - {
    SignalStatus.EXPIRED,
    SignalStatus.IGNORED,
    SignalStatus.DUPLICATE,
} | {SignalStatus.READY, SignalStatus.VALIDATING, SignalStatus.EXECUTION_PENDING}


class SignalStateMachine:
    @staticmethod
    def initial_status(*, has_stop_loss: bool) -> SignalStatus:
        """Status immediately after the entry (BUY/SELL/...) message.

        If the very first message already carries a Stop Loss (e.g.
        "BUY GOLD SL 4623" in one line) the signal is READY immediately.
        """
        return SignalStatus.READY if has_stop_loss else SignalStatus.WAITING_FOR_DETAILS

    @staticmethod
    def apply_details(current_status: SignalStatus, *, has_stop_loss: bool) -> SignalStatus:
        """Status after a follow-up message that may carry the Stop Loss."""
        if current_status not in OPEN_STATUSES:
            return current_status
        if has_stop_loss:
            return SignalStatus.READY
        return SignalStatus.WAITING_FOR_DETAILS

    @staticmethod
    def is_expired(
        current_status: SignalStatus,
        *,
        first_message_at: datetime,
        timeout_seconds: int,
        now: datetime,
    ) -> bool:
        if current_status not in OPEN_STATUSES:
            return False
        return now - first_message_at >= timedelta(seconds=timeout_seconds)

    @staticmethod
    def expire(current_status: SignalStatus) -> SignalStatus:
        if current_status not in OPEN_STATUSES:
            return current_status
        return SignalStatus.EXPIRED

    @staticmethod
    def is_terminal(status: SignalStatus) -> bool:
        return status in TERMINAL_STATUSES
