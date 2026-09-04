"""Copy one MT5 account's trading settings onto another account
(2026-09-04, written for the MetaQuotes -> Axi switch).

Settings are per-account (see TradingSettings' docstring), and a brand-new
account starts from code defaults -- so registering a replacement account
and deleting the old one silently discards a strategy that was tuned over
weeks (lot size, the daily/weekly lot plan, the channel-stop filter, the
fallback Stop Loss...). Run this in between the two steps and the new
account carries the old one's strategy across unchanged.

Every field goes through `SettingsService.update`, so the copy is audited
like any other settings change, and switching an account into LIVE mode
still refuses without the confirmation phrase (a deliberate wall this
script does not tunnel through -- flip that in the dashboard yourself).

Usage (from the backend/ directory, with the project venv active):

    python scripts/copy_settings.py <source> <target>

Each argument is an MT5 login, optionally pinned to a server with `@` when
the same login is registered more than once (the pair is what's unique,
and case-sensitively so):

    python scripts/copy_settings.py 111715721@MetaQuotes-Demo 50071008
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app.accounts.service import AccountService  # noqa: E402
from app.database.models.mt5_account import MT5Account  # noqa: E402
from app.database.session import get_engine  # noqa: E402
from app.settings.schemas import TradingSettingsUpdate  # noqa: E402
from app.settings.service import LiveModeConfirmationError, SettingsService  # noqa: E402

#: Everything TradingSettingsUpdate can carry except the LIVE-mode
#: confirmation, which is a per-call gesture, not a stored setting.
_COPIED_FIELDS = [f for f in TradingSettingsUpdate.model_fields if f != "confirm_live_phrase"]


def _resolve(session: Session, spec: str, role: str) -> MT5Account:
    login, _, server = spec.partition("@")
    matches = AccountService(session).find_by_login(login.strip(), server.strip() or None)
    if not matches:
        print(f"No {role} account registered for {spec!r}.")
        raise SystemExit(1)
    if len(matches) > 1:
        print(f"{spec!r} matches more than one {role} account:")
        for account in matches:
            print(f"  * {account.mt5_login}@{account.mt5_server}  (id={account.id}, "
                  f"label={account.label!r})")
        first = matches[0]
        print(f"Pin one with `<login>@<server>`, e.g. {first.mt5_login}@{first.mt5_server}")
        raise SystemExit(1)
    return matches[0]


def main() -> None:
    args = sys.argv[1:]
    if len(args) != 2 or args[0] in {"-h", "--help"}:
        print(__doc__)
        raise SystemExit(0 if args and args[0] in {"-h", "--help"} else 1)

    with Session(get_engine()) as session:
        source = _resolve(session, args[0], "source")
        target = _resolve(session, args[1], "target")
        assert source.id is not None and target.id is not None
        if source.id == target.id:
            print("Source and target are the same account -- nothing to do.")
            raise SystemExit(1)

        service = SettingsService(session)
        src_settings = service.get_for_account(source.id)
        dst_settings = service.get_for_account(target.id)

        values = {field: getattr(src_settings, field) for field in _COPIED_FIELDS}
        differing = {
            field: (getattr(dst_settings, field), value)
            for field, value in values.items()
            if getattr(dst_settings, field) != value
        }

        print(f"Copying settings  {source.label!r} ({source.mt5_login})")
        print(f"              ->  {target.label!r} ({target.mt5_login})")
        if not differing:
            print("\nThe two accounts already have identical settings -- nothing to change.")
            return
        print("\nChanges to the target account:")
        for field, (old, new) in differing.items():
            print(f"  {field}: {old} -> {new}")
        if values.get("auto_trading"):
            print(
                "\nNOTE: auto_trading is being copied as ON -- the target account starts "
                "executing signals as soon as its process is connected."
            )

        if input("\nApply? [y/N] ").strip().lower() != "y":
            print("Nothing was changed.")
            raise SystemExit(1)

        try:
            service.update(target.id, TradingSettingsUpdate(**values))
        except LiveModeConfirmationError:
            print(
                "REFUSED: the source account is in LIVE mode. Everything except `mode` is "
                "unchanged -- switch the target to LIVE from the dashboard yourself, where "
                "the confirmation phrase is required."
            )
            raise SystemExit(1) from None

        target.updated_at = datetime.now(UTC)
        session.add(target)
        session.commit()

    print(f"\nDone -- {len(differing)} setting(s) copied.")


if __name__ == "__main__":
    main()
