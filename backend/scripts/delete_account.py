"""Permanently delete one MT5 account registration, with every trade and
settings row that points at it (2026-09-04 operator request: the seeded
`MetaQuotes-Demo` account was retired outright, not archived).

Run it yourself, directly in your own terminal on the machine holding the
database -- there is deliberately no delete endpoint in the API and no
button in the dashboard. `AccountService.archive` (the /accounts page's
"أرشفة") is the reversible, everyday way to retire an account; this script
is the irreversible one, kept out of every remote surface so it can only
ever be triggered by someone standing in front of the database.

What it removes, in one transaction:
  * every `trades` row with this account_id  (its whole trade history)
  * every `trading_settings` row with this account_id  (its strategy)
  * the `mt5_accounts` row itself

Shared signal history (`signals`, `incoming_messages`, `archived_signals`)
is NOT account-scoped and is left completely untouched -- it belongs to the
Telegram channel, not to any one account.

Refuses while a process is currently connected to the account (same guard
as archive): stop that process first, otherwise it would keep running
against a row that no longer exists.

Usage (from the backend/ directory, with the project venv active):

    python scripts/delete_account.py 111715721
    python scripts/delete_account.py 111715721 MetaQuotes-Demo

The server argument is optional, and only needed to disambiguate when the
same login number exists on more than one server.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from app.accounts.service import AccountService  # noqa: E402
from app.audit.service import AuditService  # noqa: E402
from app.core.constants import AuditEventType, AuditSeverity  # noqa: E402
from app.database.models.mt5_account import is_account_connected  # noqa: E402
from app.database.models.trade import Trade  # noqa: E402
from app.database.models.trading_settings import TradingSettings  # noqa: E402
from app.database.models.user import User  # noqa: E402
from app.database.session import get_engine  # noqa: E402


def _usage() -> None:
    print(__doc__)


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in {"-h", "--help"}:
        _usage()
        raise SystemExit(0 if args else 1)

    mt5_login = args[0].strip()
    mt5_server = args[1].strip() if len(args) > 1 else None

    with Session(get_engine()) as session:
        matches = AccountService(session).find_by_login(mt5_login, mt5_server)

        if not matches:
            where = f"{mt5_login!r}" + (f" on {mt5_server!r}" if mt5_server else "")
            print(f"No MT5 account registered for login {where} -- nothing to delete.")
            raise SystemExit(1)
        if len(matches) > 1:
            print(f"Login {mt5_login!r} exists on more than one server:")
            for account in matches:
                print(f"  * {account.mt5_server}  (id={account.id}, label={account.label!r})")
            print("Re-run with the server as a second argument to pick one.")
            raise SystemExit(1)

        account = matches[0]
        assert account.id is not None
        owner = session.get(User, account.user_id)
        trades = list(session.exec(select(Trade).where(Trade.account_id == account.id)))
        settings_rows = list(
            session.exec(select(TradingSettings).where(TradingSettings.account_id == account.id))
        )

        print("About to PERMANENTLY delete:")
        print(f"  account id       : {account.id}")
        print(f"  label            : {account.label}")
        print(f"  login / server   : {account.mt5_login} / {account.mt5_server}")
        print(f"  symbol           : {account.symbol}")
        print(f"  owner            : {owner.username if owner else '(unknown)'}")
        print(f"  shared with all  : {account.is_shared}")
        print(f"  trades           : {len(trades)}")
        print(f"  settings rows    : {len(settings_rows)}")
        print("This cannot be undone.")

        if is_account_connected(account):
            print(
                "\nREFUSED: a process is connected to this account right now "
                f"(last heartbeat {account.last_heartbeat_at}). Stop it first, then re-run."
            )
            raise SystemExit(1)

        confirmation = input(f"\nType the login number ({mt5_login}) to confirm: ").strip()
        if confirmation != mt5_login:
            print("Confirmation didn't match -- nothing was deleted.")
            raise SystemExit(1)

        label, login, server, user_id = (
            account.label,
            account.mt5_login,
            account.mt5_server,
            account.user_id,
        )
        for trade in trades:
            session.delete(trade)
        for settings_row in settings_rows:
            session.delete(settings_row)
        session.delete(account)
        session.commit()

        # After the commit, so the trail can never claim a deletion that
        # rolled back. This entry is the only surviving record of it.
        AuditService(session).log(
            AuditEventType.ACCOUNT_DELETED,
            f"MT5 account '{label}' (login={login}, server={server}) permanently deleted "
            f"with {len(trades)} trade(s) and {len(settings_rows)} settings row(s).",
            severity=AuditSeverity.WARNING,
            metadata={"mt5_login": login, "mt5_server": server, "user_id": user_id},
        )

    print(f"\nDeleted account {login} ({label}) and everything that pointed at it.")


if __name__ == "__main__":
    main()
