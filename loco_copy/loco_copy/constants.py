from __future__ import annotations

from enum import StrEnum


class EventType(StrEnum):
    OPEN = "OPEN"
    ADD = "ADD"
    REDUCE = "REDUCE"
    CLOSE = "CLOSE"
    #: The closing half of a sign flip; the tracker emits it immediately followed by an OPEN for the new side.
    FLIP = "FLIP"


class CopyStatus(StrEnum):
    OBSERVED_ONLY = "OBSERVED_ONLY"
    SUBMITTING = "SUBMITTING"
    OPEN = "OPEN"
    CLOSED_LEADER = "CLOSED_LEADER"
    CLOSED_TP = "CLOSED_TP"
    CLOSED_LIQ = "CLOSED_LIQ"
    CLOSED_MANUAL = "CLOSED_MANUAL"
    CLOSED_RECONCILED = "CLOSED_RECONCILED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


#: A copy in one of these still holds (or may hold) a position on the exchange.
LIVE_STATUSES = frozenset({CopyStatus.SUBMITTING, CopyStatus.OPEN})


class Decision(StrEnum):
    """What was done with one leader event (the `events.decision` column)."""

    COPIED = "COPIED"
    FOLLOWED = "FOLLOWED"  # an exit (reduce/close/flip) was mirrored
    OBSERVED_ONLY = "OBSERVED_ONLY"
    REJECTED = "REJECTED"
    IGNORED_ADD = "IGNORED_ADD"
    IGNORED_REDUCE = "IGNORED_REDUCE"  # FOLLOW_REDUCES=false
    NO_MATCHING_COPY = "NO_MATCHING_COPY"
    FAILED = "FAILED"
    # cap20_cross (botup.md v4 §5)
    ADDED = "ADDED"  # an add was copied (dq x R)
    ADD_PENDING = "ADD_PENDING"  # below the minimum order: carried to the next add
    ADD_CAPPED = "ADD_CAPPED"  # the trade reached its margin cap: adds ignored, reduces still followed
    REDUCED = "REDUCED"  # the leader's reduce was followed with the same fraction
    CLOSED = "CLOSED"  # the leader's close / flip was followed: ours closed 100 %


class Rejection(StrEnum):
    DISABLED = "DISABLED"
    KILL_SWITCH = "KILL_SWITCH"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    LEADER_BREAKER = "LEADER_BREAKER"
    LATENCY_BREAKER = "LATENCY_BREAKER"
    UNSUPPORTED_MARKET = "UNSUPPORTED_MARKET"
    PRE_EXISTING_POSITION = "PRE_EXISTING_POSITION"
    SIDE_DISABLED = "SIDE_DISABLED"
    COIN_BLOCKED = "COIN_BLOCKED"
    SYMBOL_NOT_LISTED = "SYMBOL_NOT_LISTED"
    SIGNAL_TOO_OLD = "SIGNAL_TOO_OLD"
    PRICE_DRIFTED = "PRICE_DRIFTED"
    DUPLICATE_EPISODE = "DUPLICATE_EPISODE"
    #: One-way mode holds one position per symbol, so a still-open copy from an earlier episode on the same
    #: coin blocks a new one (otherwise the two would silently merge on the exchange).
    COIN_ALREADY_OPEN = "COIN_ALREADY_OPEN"
    #: Two leaders on one exchange account: a symbol another lane really holds would merge with ours.
    COIN_HELD_BY_OTHER_LEADER = "COIN_HELD_BY_OTHER_LEADER"
    #: This leader's lane is switched off on the dashboard (new opens only -- exits always follow).
    LANE_PAUSED = "LANE_PAUSED"
    #: Lane mode is EXECUTE but ALLOW_EXECUTE__<LABEL> is false: the open is recorded as observed, never sent.
    EXECUTION_NOT_ALLOWED = "EXECUTION_NOT_ALLOWED"
    #: cap20: no good Hyperliquid contact for FEED_OUTAGE_BLOCK_SECONDS -> no new trades / adds.
    FEED_OUTAGE = "FEED_OUTAGE"
    #: cap20: total open notional would exceed MAX_TOTAL_NOTIONAL_X_EQUITY x equity.
    EXPOSURE_LIMIT = "EXPOSURE_LIMIT"
    #: v5: the shared account fell below (1 - ACCOUNT_KILL_DD_PCT) x its peak.
    ACCOUNT_KILL = "ACCOUNT_KILL"
    #: cap20 needs the leader's equity W0 to size a trade; none fresh enough.
    LEADER_EQUITY_UNKNOWN = "LEADER_EQUITY_UNKNOWN"
    BROKER_UNAVAILABLE = "BROKER_UNAVAILABLE"
    MAX_OPEN_POSITIONS = "MAX_OPEN_POSITIONS"
    INSUFFICIENT_MARGIN = "INSUFFICIENT_MARGIN"
    NOTIONAL_CAP = "NOTIONAL_CAP"
    QTY_TOO_SMALL = "QTY_TOO_SMALL"


class OrderKind(StrEnum):
    OPEN = "OPEN"
    REDUCE = "REDUCE"
    CLOSE = "CLOSE"


class OrderStatus(StrEnum):
    SENT = "SENT"
    FILLED = "FILLED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


LIVE_CONFIRM_PHRASE = "I ACCEPT REAL MONEY RISK"
