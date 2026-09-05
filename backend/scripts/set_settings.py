"""Apply named trading settings to one account from the command line
(2026-09-05).

The dashboard is the everyday way to change these. This exists for the
case it handles badly: the same few values across several accounts, where
switching accounts in the UI and retyping each field is where a wrong
number lands on the wrong account. Every change still goes through
`SettingsService.update`, so it is audited exactly like a dashboard edit
and the LIVE-mode confirmation wall still stands.

Prints the before -> after of every field that would actually change and
asks before writing; a field already at the requested value is reported as
unchanged rather than rewritten.

Usage (from the backend/ directory, with the project venv active):

    python scripts/set_settings.py <account> <field>=<value> [<field>=<value> ...]

`<account>` is an MT5 login, optionally `login@server` when the same login
is registered on more than one server. Examples:

    python scripts/set_settings.py 50071008 target_pips=60 lot_size=1
    python scripts/set_settings.py 450137654 max_open_trades=5
    python scripts/set_settings.py 50071008 daily_lot_plan_enabled=false
    python scripts/set_settings.py 50071008 auto_sl_fallback_pips=none

Booleans take true/false (also on/off, yes/no, 1/0). The two nullable
fields (max_channel_stop_pips, auto_sl_fallback_pips) take `none` to
disable them. `--yes` skips the confirmation prompt.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]

from pydantic import ValidationError  # noqa: E402
from sqlmodel import Session  # noqa: E402

from app.accounts.service import AccountService  # noqa: E402
from app.core.decimals import fmt  # noqa: E402
from app.database.models.mt5_account import MT5Account  # noqa: E402
from app.database.session import get_engine  # noqa: E402
from app.settings.schemas import TradingSettingsUpdate  # noqa: E402
from app.settings.service import LiveModeConfirmationError, SettingsService  # noqa: E402

#: `confirm_live_phrase` is a per-call gesture, not a stored setting -- it
#: must not be settable as if it were one.
_FIELDS = [f for f in TradingSettingsUpdate.model_fields if f != "confirm_live_phrase"]
#: Fields where `None` is a real, meaningful value ("disable this filter"),
#: not merely "unset" -- see TradingSettingsUpdate's own comments.
_NULLABLE = {"max_channel_stop_pips", "auto_sl_fallback_pips"}


def _resolve(session: Session, spec: str) -> MT5Account:
    login, _, server = spec.partition("@")
    matches = AccountService(session).find_by_login(login.strip(), server.strip() or None)
    if not matches:
        print(f"لا يوجد حساب مسجّل بـ {spec!r}.")
        raise SystemExit(1)
    if len(matches) > 1:
        print(f"{spec!r} يطابق أكثر من حساب:")
        for account in matches:
            print(f"  * {account.mt5_login}@{account.mt5_server} (id={account.id})")
        print("حدّد واحدًا بالصيغة <login>@<server>.")
        raise SystemExit(1)
    return matches[0]


def _parse(pairs: list[str]) -> dict[str, object]:
    values: dict[str, object] = {}
    for pair in pairs:
        if "=" not in pair:
            print(f"{pair!r} ليس بصيغة <field>=<value>.")
            raise SystemExit(1)
        field, _, raw = pair.partition("=")
        field, raw = field.strip(), raw.strip()
        if field not in _FIELDS:
            print(f"لا يوجد إعداد باسم {field!r}. الإعدادات المتاحة:")
            print("  " + ", ".join(_FIELDS))
            raise SystemExit(1)
        if raw.lower() in {"none", "null"}:
            if field not in _NULLABLE:
                print(f"{field!r} لا يقبل القيمة none.")
                raise SystemExit(1)
            values[field] = None
        else:
            values[field] = raw  # pydantic does the typing/validation below
    return values


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--yes"]
    assume_yes = "--yes" in sys.argv[1:]
    if len(args) < 2 or args[0] in {"-h", "--help"}:
        print(__doc__)
        raise SystemExit(0 if args and args[0] in {"-h", "--help"} else 1)

    values = _parse(args[1:])
    try:
        patch = TradingSettingsUpdate(**values)  # type: ignore[arg-type]
    except ValidationError as exc:
        print("قيمة غير صالحة:")
        for error in exc.errors():
            print(f"  {'.'.join(str(p) for p in error['loc'])}: {error['msg']}")
        raise SystemExit(1) from None

    with Session(get_engine()) as session:
        account = _resolve(session, args[0])
        assert account.id is not None
        service = SettingsService(session)
        settings = service.get_for_account(account.id)

        changes = {
            field: (getattr(settings, field), value)
            for field, value in patch.model_dump(exclude_unset=True).items()
            if getattr(settings, field) != value
        }

        print(f"الحساب [id={account.id}] {account.label} "
              f"({account.mt5_login}@{account.mt5_server})")
        if not changes:
            print("كل القيم المطلوبة مضبوطة أصلًا — لا تغيير.")
            return
        print("\nالتغييرات:")
        for field, (old, new) in changes.items():
            print(f"  {field}: {fmt(old)} -> {fmt(new)}")

        if not assume_yes and input("\nتطبيق؟ [y/N] ").strip().lower() != "y":
            print("لم يُغيَّر شيء.")
            raise SystemExit(1)

        try:
            service.update(account.id, patch)
        except LiveModeConfirmationError:
            print(
                "مرفوض: التحويل إلى وضع LIVE يتطلب عبارة التأكيد — نفّذه من اللوحة، "
                "حيث تُطلب صراحةً. باقي الحقول لم تتغيّر."
            )
            raise SystemExit(1) from None

    print(f"\nتم تطبيق {len(changes)} إعداد.")


if __name__ == "__main__":
    main()
