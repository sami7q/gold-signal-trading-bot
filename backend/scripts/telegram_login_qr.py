"""One-time interactive Telegram login via QR link (PROJECT_SPEC.md Phase 11).

Alternative to scripts/telegram_login.py for when you want to log the bot
into a DIFFERENT Telegram account than the one currently active, using a
login link/QR code instead of typing a phone number and confirmation code.

Run this yourself, directly in your own terminal — never through an
automated agent, a script, the dashboard, or any HTTP endpoint. The link
this prints authorizes whichever Telegram account opens it, so only open it
from the account you actually want the bot to log in as.

Usage (from the backend/ directory, with the project venv active):

    python scripts/telegram_login_qr.py

What happens:
  1. It prints a link (tg://login?token=...).
  2. Open that exact link FROM THE TELEGRAM ACCOUNT you want the bot to use
     -- e.g. paste it into that account's browser, or open it on a phone/
     desktop where that account is already logged in. Telegram will show a
     "Confirm login?" prompt; confirm it.
  3. If that account has a 2FA password, this script will ask for it here
     (never anywhere else). Nothing you type is logged or saved by this
     script.
  4. The link expires after ~30 seconds; this script automatically
     generates a fresh one and reprints it if that happens, no restart
     needed.

After a successful login it lists every chat/channel/group visible to that
account, with its numeric id — copy the id of your signals channel into
TELEGRAM_ALLOWED_CHANNEL_ID in the project's .env.

Requires TELEGRAM_API_ID and TELEGRAM_API_HASH to already be set in the
project's .env (get them from https://my.telegram.org -> API Development
Tools). TELEGRAM_ENABLED does NOT need to be true to run this script.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings  # noqa: E402


async def _main() -> None:
    settings = get_settings()

    if not settings.telegram_api_id or not settings.telegram_api_hash:
        print(
            "TELEGRAM_API_ID / TELEGRAM_API_HASH are not set in .env.\n"
            "Get them from https://my.telegram.org -> API Development Tools, "
            "add them to the project's .env file, then run this script again."
        )
        raise SystemExit(1)

    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError
    from telethon.tl.types import Channel, Chat, User

    session_path = settings.telegram_session_path or "telegram_session"
    client = TelegramClient(session_path, int(settings.telegram_api_id), settings.telegram_api_hash)

    print(f"Session file: {session_path}.session")
    await client.connect()

    if await client.is_user_authorized():
        print(
            "This session is already logged in to an account. If you meant to "
            "switch accounts, delete the .session file above first and rerun "
            "this script."
        )
        await client.disconnect()
        return

    qr = await client.qr_login()
    attempts = 0
    me = None
    while me is None:
        attempts += 1
        print(f"\nOpen this link FROM THE TELEGRAM ACCOUNT you want to use (attempt {attempts}):")
        print(qr.url)
        print("(expires in ~30s -- a fresh link prints automatically if it does)")
        try:
            me = await qr.wait(timeout=30)
        except TimeoutError:
            await qr.recreate()
        except SessionPasswordNeededError:
            import getpass

            password = getpass.getpass("2FA password for that account: ")
            me = await client.sign_in(password=password)

    first_name = getattr(me, "first_name", "")
    username = getattr(me, "username", None)
    print(f"\nLogged in as: {first_name} (@{username})\n")
    print("Your chats/channels (name -> id):\n")
    async for dialog in client.iter_dialogs():
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

    await client.disconnect()
    print("\nDone. Copy the id of your signals channel into TELEGRAM_ALLOWED_CHANNEL_ID in .env.")


if __name__ == "__main__":
    asyncio.run(_main())
