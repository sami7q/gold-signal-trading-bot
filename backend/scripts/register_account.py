"""Register the MT5 account this process's own `.env` points at, as a row
in `mt5_accounts` (2026-09-04).

The dashboard's /accounts form is the everyday way to add an account, and
it stays that way. This script exists for the one case the form is bad at:
the account a *process* must resolve as itself. That match is literal and
case-sensitive on both fields (`AccountService.resolve_own_process_account`
-- `MetaQuotes-Demo` and `metaquotes-demo` are two different servers, which
is how one demo account really did end up registered twice on the VPS), and
a form retyped by hand is exactly where that goes wrong. Here the row is
built from the same `MT5_ACCOUNT`/`MT5_SERVER`/`MT5_PASSWORD`/`MT5_SYMBOL`
values the broker connection itself will use, so the two cannot disagree.

The password is read from the env file this process already loads and
stored Fernet-encrypted (app/core/credentials.py) -- never typed on a
command line, never written to a migration.

Usage (from the backend/ directory, with the project venv active):

    python scripts/register_account.py <username> "<label>" [--shared]

`--shared` makes the account visible to, and usable by, every operator
(MT5Account.is_shared) -- for one account the two of them run jointly.
Without it the account is private to <username>, which is the default for
every account.

A worker process registers its own account the same way, with its own env
file:  set GOLDBOT_ENV_FILE=C:\\gold-bot\\.env.worker-450137654
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]

from sqlmodel import Session, select  # noqa: E402

from app.accounts.schemas import MT5AccountCreate  # noqa: E402
from app.accounts.service import AccountService, DuplicateAccountError  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.credentials import CredentialsNotConfiguredError  # noqa: E402
from app.database.models.user import User  # noqa: E402
from app.database.session import get_engine  # noqa: E402


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--shared"]
    shared = "--shared" in sys.argv[1:]
    if len(args) != 2 or args[0] in {"-h", "--help"}:
        print(__doc__)
        raise SystemExit(0 if args and args[0] in {"-h", "--help"} else 1)
    username, label = args[0].strip(), args[1].strip()

    settings = get_settings()
    missing = [
        name
        for name, value in (
            ("MT5_ACCOUNT", settings.mt5_account),
            ("MT5_SERVER", settings.mt5_server),
            ("MT5_PASSWORD", settings.mt5_password),
        )
        if not value
    ]
    if missing:
        print(f"لا يمكن التسجيل: {', '.join(missing)} فارغ في ملف .env الذي تقرأه هذه العملية.")
        raise SystemExit(1)

    with Session(get_engine()) as session:
        user = session.exec(select(User).where(User.username == username)).first()
        if user is None or user.id is None:
            print(f"لا يوجد مستخدم باسم {username!r}. أنشئه أولًا بـ scripts/seed_users.py.")
            raise SystemExit(1)

        service = AccountService(session)
        existing = service.find_by_login(settings.mt5_account, settings.mt5_server)
        if existing:
            account = existing[0]
            print(
                f"مسجَّل مسبقًا: [id={account.id}] {account.label} "
                f"({account.mt5_login}@{account.mt5_server}, "
                f"مشترك: {'نعم' if account.is_shared else 'لا'})."
            )
            print("عدّله من اللوحة (أو احذفه بـ scripts/delete_account.py) بدل تسجيله مرة أخرى.")
            raise SystemExit(1)

        print("سيُسجَّل هذا الحساب من قيم .env نفسها التي سيتصل بها البوت:")
        print(f"  رقم الحساب / الخادم : {settings.mt5_account} / {settings.mt5_server}")
        print(f"  الرمز               : {settings.mt5_symbol}")
        print("  كلمة المرور         : (من .env، تُخزَّن مشفّرة — لا تُطبع)")
        print(f"  المالك              : {username}")
        print(f"  الاسم المعروض       : {label}")
        print(f"  مشترك مع الجميع     : {'نعم' if shared else 'لا'}")
        if input("\nتسجيل؟ [y/N] ").strip().lower() != "y":
            print("لم يُسجَّل شيء.")
            raise SystemExit(1)

        try:
            account = service.create(
                user.id,
                MT5AccountCreate(
                    label=label,
                    mt5_login=settings.mt5_account,
                    mt5_server=settings.mt5_server,
                    mt5_password=settings.mt5_password,
                    symbol=settings.mt5_symbol,
                    is_shared=shared,
                ),
            )
        except CredentialsNotConfiguredError as exc:
            print(f"\nفشل التشفير: {exc}")
            raise SystemExit(1) from None
        except DuplicateAccountError as exc:
            print(f"\n{exc}")
            raise SystemExit(1) from None

        # Read inside the session: `commit()` expires the instance, and
        # outside the `with` it is detached, so touching an attribute there
        # raises DetachedInstanceError instead of printing.
        created = f"[id={account.id}] {account.label}"

    print(f"\nتم: {created}")
    print("التالي: انقل الإعدادات بـ scripts/copy_settings.py، ثم أعد تشغيل العملية،")
    print("ثم تحقّق بـ scripts/check_accounts.py.")


if __name__ == "__main__":
    main()
