"""One-time interactive Telegram login (PROJECT_SPEC.md Phase 11, section 2).

Run this yourself, directly in your own terminal — never through an
automated agent, a script, the dashboard, or any HTTP endpoint. It will ask
you for your phone number, the confirmation code Telegram sends you, and
your 2FA password if you have one enabled. None of that is ever written to
a file or log by this script; Telethon only persists the resulting session
(a login token), not your credentials.

Usage (from the backend/ directory, with the project venv active):

    python scripts/telegram_login.py

After a successful login it lists every chat/channel/group your account can
see, with its numeric id — copy the id of your signals channel into
TELEGRAM_ALLOWED_CHANNEL_ID in the project's .env file.

Requires TELEGRAM_API_ID and TELEGRAM_API_HASH to already be set in the
project's .env (get them from https://my.telegram.org -> API Development
Tools). TELEGRAM_ENABLED does NOT need to be true to run this script.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings  # noqa: E402


def main() -> None:
    settings = get_settings()

    if not settings.telegram_api_id or not settings.telegram_api_hash:
        print(
            "TELEGRAM_API_ID / TELEGRAM_API_HASH are not set in .env.\n"
            "Get them from https://my.telegram.org -> API Development Tools, "
            "add them to the project's .env file, then run this script again."
        )
        raise SystemExit(1)

    from telethon import TelegramClient
    from telethon.tl.types import Channel, Chat, User

    session_path = settings.telegram_session_path or "telegram_session"
    client = TelegramClient(session_path, int(settings.telegram_api_id), settings.telegram_api_hash)

    print(f"Session file: {session_path}.session")
    print("Connecting... you'll be prompted for your phone number, then the")
    print("confirmation code Telegram sends you, then your 2FA password if you")
    print("have one. Nothing you type here is logged or saved by this script.\n")

    with client:
        me = client.get_me()
        first_name = getattr(me, "first_name", "")
        username = getattr(me, "username", None)
        print(f"\nLogged in as: {first_name} (@{username})\n")
        print("Your chats/channels (name -> id):\n")
        for dialog in client.iter_dialogs():
            entity = dialog.entity
            kind = (
                "channel"
                if isinstance(entity, Channel) and entity.broadcast
                else "group"
                if isinstance(entity, (Channel, Chat))
                else "user"
                if isinstance(entity, User)
                else "other"
            )
            print(f"  [{kind:7}] {dialog.name!r:50} id={dialog.id}")

    print("\nDone. Copy the id of your signals channel into TELEGRAM_ALLOWED_CHANNEL_ID in .env.")


if __name__ == "__main__":
    main()
