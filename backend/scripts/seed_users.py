"""One-time interactive dashboard-login setup (multi-account feature, 2026-09).

Run this yourself, directly in your own terminal on the VPS, right after
`alembic upgrade head` has applied `5c2d5f5986c7_add_multi_account_tables.py`
-- never through an automated agent, a script, the dashboard, or any HTTP
endpoint. Passwords are read with `getpass` (never echoed, never written to
a file or log by this script) and stored only as a PBKDF2 hash
(`app.core.security.hash_password`); the raw password itself is never
persisted anywhere.

There is no public signup endpoint anywhere in this codebase -- this script
is the only way a dashboard login is ever created or its password changed.

Usage (from the backend/ directory, with the project venv active):

    python scripts/seed_users.py

For each of "sami" and "mustafa" it asks whether you want to set/reset that
user's password (skip the ones you don't want to touch), creating the user
row first if it doesn't exist yet. "sami" already exists after the
migration above (it seeded the one MT5 account already live on the VPS) --
running this script the first time is what turns that placeholder row into
an actual working login.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from getpass import getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.database.models.user import User  # noqa: E402
from app.database.session import get_engine  # noqa: E402

#: (username, display_name) -- the two operators this feature was built
#: for (see the approved multi-account plan). Add more here later if a
#: third operator joins; nothing else about this script is username-specific.
_SEED_USERNAMES: list[tuple[str, str]] = [
    ("sami", "Sami"),
    ("mustafa", "Mustafa"),
]


def _prompt_password(username: str) -> str | None:
    answer = input(f"Set/reset password for '{username}'? [y/N] ").strip().lower()
    if answer != "y":
        return None
    while True:
        password = getpass(f"  New password for '{username}': ")
        if len(password) < 8:
            print("  Password must be at least 8 characters -- try again.")
            continue
        confirm = getpass(f"  Confirm password for '{username}': ")
        if password != confirm:
            print("  Passwords didn't match -- try again.")
            continue
        return password


def main() -> None:
    with Session(get_engine()) as session:
        for username, display_name in _SEED_USERNAMES:
            user = session.exec(select(User).where(User.username == username)).first()
            if user is None:
                print(f"Creating new user '{username}' ({display_name})...")
                user = User(
                    username=username,
                    display_name=display_name,
                    # Same "impossible to match" sentinel the migration
                    # seeds "sami" with -- overwritten below the moment a
                    # real password is set, but never left blank/guessable
                    # if this prompt is skipped for a brand-new user.
                    password_hash="unset:pending-seed-users-script",
                )
                session.add(user)
                session.commit()
                session.refresh(user)
            else:
                print(f"Found existing user '{username}' (id={user.id}).")

            password = _prompt_password(username)
            if password is None:
                print(f"  Skipped -- '{username}' password left unchanged.\n")
                continue

            user.password_hash = hash_password(password)
            user.is_active = True
            user.updated_at = datetime.now(UTC)
            session.add(user)
            session.commit()
            print(f"  Password set for '{username}'.\n")

    print("Done. Log in at the dashboard's /login page with these credentials.")


if __name__ == "__main__":
    main()
