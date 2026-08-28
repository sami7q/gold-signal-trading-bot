"""Targeted audit: dumps every raw message in a specific date window,
tagged with its parser classification, so we can manually cross-check a
channel's own "weekly results" summary against what we actually captured
as real entry signals.

Usage (from backend/): python scripts/audit_message_coverage.py --from 2026-01-04 --to 2026-01-12
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from app.core.config import get_settings  # noqa: E402
from app.signals.models import IncomingMessageData  # noqa: E402
from app.signals.parser import parse_message  # noqa: E402


async def _fetch_window(
    channel_id: int, since: datetime, until: datetime
) -> list[IncomingMessageData]:
    from telethon import TelegramClient

    settings = get_settings()
    client = TelegramClient(
        settings.telegram_session_path or "telegram_session",
        int(settings.telegram_api_id),
        settings.telegram_api_hash,
    )
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError("No authorized Telegram session found.")

    messages: list[IncomingMessageData] = []
    async for msg in client.iter_messages(channel_id, offset_date=since, reverse=True):
        if msg.date < since:
            continue
        if msg.date > until:
            break
        messages.append(
            IncomingMessageData(
                source="telegram_audit",
                channel_id=str(channel_id),
                message_id=str(msg.id),
                text=msg.raw_text or "",
                sent_at=msg.date,
                received_at=msg.date,
                reply_to_message_id=str(msg.reply_to_msg_id) if msg.reply_to_msg_id else None,
            )
        )
    await client.disconnect()
    return messages


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="date_from", required=True)
    parser.add_argument("--to", dest="date_to", required=True)
    args = parser.parse_args()

    settings = get_settings()
    channel_id = int(settings.telegram_allowed_channel_id)
    since = datetime.strptime(args.date_from, "%Y-%m-%d").replace(tzinfo=UTC)
    until = datetime.strptime(args.date_to, "%Y-%m-%d").replace(tzinfo=UTC)

    print(f"Fetching messages for channel {channel_id} from {since.date()} to {until.date()}...")
    messages = asyncio.run(_fetch_window(channel_id, since, until))
    print(f"Total raw messages in window: {len(messages)}\n")

    for m in messages:
        parsed = parse_message(m.text)
        kind = parsed.kind.value if parsed.kind else "-"
        text = m.text.replace("\n", " | ")
        cls = parsed.classification.value
        print(f"[{m.sent_at}] id={m.message_id} class={cls:<16} kind={kind:<10} :: {text}")


if __name__ == "__main__":
    main()
