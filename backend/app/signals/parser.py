"""Deterministic signal message parser (PROJECT_SPEC.md section 6).

Regex + explicit rules only. No LLM / generative AI is used anywhere in this
module because it decides what may eventually become a real trade — see
PROJECT_SPEC.md section 32, rule 1 and section 21 (Operational Safety).
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.core.constants import (
    CANONICAL_GOLD_SYMBOL,
    GOLD_ALIASES,
    MessageClassification,
    SignalKind,
)
from app.signals.classifier import determine_role
from app.signals.models import ParsedMessage

_NUMBER = r"\d+(?:\.\d+)?"

_GOLD_ALIAS_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(alias) for alias in GOLD_ALIASES) + r")\b"
)

# Order matters: compound kinds (BUY LIMIT / SELL STOP / ...) must be tried
# before the bare BUY / SELL patterns, otherwise "BUY LIMIT GOLD" would be
# misread as a plain BUY.
_KIND_PATTERNS: list[tuple[re.Pattern[str], SignalKind]] = [
    (re.compile(r"\bBUY\s+LIMIT\b"), SignalKind.BUY_LIMIT),
    (re.compile(r"\bSELL\s+LIMIT\b"), SignalKind.SELL_LIMIT),
    (re.compile(r"\bBUY\s+STOP\b"), SignalKind.BUY_STOP),
    (re.compile(r"\bSELL\s+STOP\b"), SignalKind.SELL_STOP),
    (re.compile(r"\bBUY\b"), SignalKind.BUY),
    (re.compile(r"\bSELL\b"), SignalKind.SELL),
]

_STOP_LOSS_PATTERN = re.compile(r"\b(?:SL|STOP\s*LOSS|STOPLOSS)\s*[:\-]?\s*(" + _NUMBER + r")\b")

_ENTRY_RANGE_PATTERN = re.compile(r"\b(" + _NUMBER + r")\s*-\s*(" + _NUMBER + r")\b")

_TARGET_LINE_PATTERN = re.compile(r"^\s*(" + _NUMBER + r")\s*$", re.MULTILINE)

_PIPS_UPDATE_PATTERN = re.compile(r"\bPIPS?\b", re.IGNORECASE)

_RESULT_KEYWORDS = re.compile(r"\b(CLOSED|HIT\s*TP|HIT\s*SL|TARGET\s*HIT|STOPPED\s*OUT|WIN|LOSS)\b")

# Strips everything except letters, digits, spaces, and the punctuation the
# grammar above actually depends on (':', '-', '/', '.', newlines).
_STRIP_PATTERN = re.compile(r"[^\w\s:\-/.]", re.UNICODE)


def normalize_text(text: str) -> str:
    """Uppercase, drop emoji/symbols, collapse repeated blank lines."""
    stripped = _STRIP_PATTERN.sub(" ", text)
    upper = stripped.upper()
    lines = [line.strip() for line in upper.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def normalize_instrument(token: str) -> str:
    return CANONICAL_GOLD_SYMBOL if token.upper() in GOLD_ALIASES else token.upper()


def is_gold_message(normalized_text: str) -> bool:
    return _GOLD_ALIAS_PATTERN.search(normalized_text) is not None


def detect_kind(normalized_text: str) -> SignalKind | None:
    for pattern, kind in _KIND_PATTERNS:
        if pattern.search(normalized_text):
            return kind
    return None


def _to_decimal(raw: str) -> Decimal | None:
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def extract_stop_loss(normalized_text: str) -> Decimal | None:
    match = _STOP_LOSS_PATTERN.search(normalized_text)
    return _to_decimal(match.group(1)) if match else None


def extract_entry_range(normalized_text: str) -> tuple[Decimal | None, Decimal | None]:
    # A stop-loss line such as "SL 4623-4620" must not be misread as an
    # entry range, so the SL segment is excluded before searching.
    text_without_sl = _STOP_LOSS_PATTERN.sub("", normalized_text)
    match = _ENTRY_RANGE_PATTERN.search(text_without_sl)
    if not match:
        return None, None
    a, b = _to_decimal(match.group(1)), _to_decimal(match.group(2))
    if a is None or b is None:
        return None, None
    return min(a, b), max(a, b)


def extract_target_prices(normalized_text: str) -> list[Decimal]:
    """Bare numeric lines (Message C in PROJECT_SPEC.md section 6.4) — stored
    as informational channel targets only, never used for execution."""
    text_without_sl = _STOP_LOSS_PATTERN.sub("", normalized_text)
    values = []
    for line_match in _TARGET_LINE_PATTERN.finditer(text_without_sl):
        value = _to_decimal(line_match.group(1))
        if value is not None:
            values.append(value)
    return values


def classify_message(
    normalized_text: str,
    *,
    kind: SignalKind | None,
    is_gold: bool,
    stop_loss: Decimal | None,
    entry_min: Decimal | None,
    target_prices: list[Decimal],
) -> MessageClassification:
    if kind is not None and is_gold:
        return MessageClassification.ENTRY_SIGNAL
    if stop_loss is not None or entry_min is not None:
        return MessageClassification.SIGNAL_DETAILS
    if target_prices or _PIPS_UPDATE_PATTERN.search(normalized_text):
        return MessageClassification.TARGET_UPDATE
    if _RESULT_KEYWORDS.search(normalized_text):
        return MessageClassification.RESULT_UPDATE
    return MessageClassification.IGNORED


def parse_message(raw_text: str) -> ParsedMessage:
    normalized = normalize_text(raw_text)

    kind = detect_kind(normalized)
    is_gold = is_gold_message(normalized)
    stop_loss = extract_stop_loss(normalized)
    entry_min, entry_max = extract_entry_range(normalized)
    target_prices = extract_target_prices(normalized)

    classification = classify_message(
        normalized,
        kind=kind,
        is_gold=is_gold,
        stop_loss=stop_loss,
        entry_min=entry_min,
        target_prices=target_prices,
    )
    role = determine_role(
        kind=kind,
        stop_loss=stop_loss,
        entry_min=entry_min,
        target_prices=target_prices,
        classification=classification,
    )

    return ParsedMessage(
        raw_text=raw_text,
        normalized_text=normalized,
        classification=classification,
        role=role,
        is_gold=is_gold,
        kind=kind,
        entry_min=entry_min,
        entry_max=entry_max,
        stop_loss=stop_loss,
        target_prices=target_prices,
    )
