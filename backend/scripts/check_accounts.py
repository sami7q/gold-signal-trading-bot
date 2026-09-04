"""Read-only status check: who sees which MT5 account, which account this
machine's own process is really connected to, and whether that connection
is a real MT5 terminal or the Paper Broker fallback (2026-09-04).

Written because none of that is answerable from the dashboard alone. The
"متصل الآن" badge is heartbeat-derived, and a hub that failed to reach its
MT5 terminal falls back to the Paper Broker (see app/lifecycle.py) which
keeps reporting healthy -- so it keeps writing heartbeats and a paper
balance, and the badge stays green while nothing real is connected. The
broker check at the end (GET /api/health/broker) is the only thing that
tells those two states apart.

Touches nothing: every lookup is a plain SELECT. In particular it does not
go through `SettingsService.get_for_account`, which would create a default
settings row as a side effect of merely looking.

Usage (from the backend/ directory, with the project venv active):

    python scripts/check_accounts.py
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# A Windows console is rarely UTF-8; Arabic labels must never crash a
# diagnostic tool, so unencodable characters degrade to '?' instead.
sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]

from sqlmodel import Session, select  # noqa: E402

from app.accounts.service import AccountService  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.database.models.mt5_account import (  # noqa: E402
    HEARTBEAT_STALE_AFTER,
    MT5Account,
    is_account_connected,
)
from app.database.models.trade import Trade  # noqa: E402
from app.database.models.trading_settings import TradingSettings  # noqa: E402
from app.database.models.user import User  # noqa: E402
from app.database.session import get_engine  # noqa: E402

OK, BAD, WARN = "[ OK ]", "[FAIL]", "[ ?? ]"
HEALTH_URL = "http://127.0.0.1:8000/api/health/broker"


def _liveness(account: MT5Account) -> str:
    if is_account_connected(account):
        assert account.last_heartbeat_at is not None
        age = int((datetime.now(UTC) - account.last_heartbeat_at).total_seconds())
        return f"متصل الآن (آخر نبض قبل {age} ث)"
    if account.last_heartbeat_at is not None:
        return f"غير متصل (آخر نبض {account.last_heartbeat_at})"
    return "لم يتصل ولا مرة"


def _print_account(session: Session, account: MT5Account, viewer: User) -> None:
    owner = (
        "ملكه"
        if account.user_id == viewer.id
        else f"مشترك (مالكه user_id={account.user_id})"
    )
    trades = len(list(session.exec(select(Trade).where(Trade.account_id == account.id))))
    settings_row = session.exec(
        select(TradingSettings).where(TradingSettings.account_id == account.id)
    ).first()
    print(f"   [id={account.id}] {account.label}")
    print(f"        {account.mt5_login}@{account.mt5_server} | {account.symbol} | {owner}")
    print(f"        {_liveness(account)} | رصيد مسجّل: {account.last_balance} | صفقات: {trades}")
    print(
        f"        مشترك: {'نعم' if account.is_shared else 'لا'} | "
        f"مؤرشف: {'نعم' if not account.is_active else 'لا'} | "
        f"إعدادات: {'محفوظة' if settings_row else 'لم تُنشأ بعد (ستبدأ افتراضية)'}"
    )
    if settings_row is not None:
        print(
            f"        لوت={settings_row.lot_size} | تداول تلقائي="
            f"{'مفعّل' if settings_row.auto_trading else 'مطفأ'} | "
            f"خطة اللوت={'مفعّلة' if settings_row.daily_lot_plan_enabled else 'مطفأة'} | "
            f"أقصى ستوب={settings_row.max_channel_stop_pips}"
        )


def _broker_health() -> tuple[str, str]:
    """(marker, line) for the active broker -- the check the dashboard
    cannot do for you."""
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=5) as response:  # noqa: S310
            body = json.loads(response.read().decode())
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return WARN, f"تعذّر سؤال الباك-إند ({HEALTH_URL}): {exc} — هل GoldBotBackend شغّالة؟"
    broker, healthy = body.get("broker"), body.get("healthy")
    if broker == "mt5" and healthy:
        return OK, "الوسيط الفعّال: MT5 حقيقي، وصحّته سليمة."
    if broker == "mt5":
        return BAD, f"الوسيط MT5 لكنه غير سليم: {body.get('detail')}"
    return BAD, (
        f"الوسيط الفعّال: {broker!r} — ليس MT5. البوت ساقط على الوسيط الورقي، "
        "أي أن الاتصال بمحطة MT5 فشل (راجع سجل التدقيق)."
    )


def main() -> None:
    settings = get_settings()
    problems: list[str] = []

    with Session(get_engine()) as session:
        service = AccountService(session)
        users = list(session.exec(select(User).order_by(User.id.asc())))  # type: ignore[union-attr]

        print("=" * 72)
        print("من يرى أي حساب (نفس مسار اللوحة تمامًا)")
        print("=" * 72)
        for user in users:
            assert user.id is not None
            visible = service.list_for_user(user.id)
            print(f"\n{user.username} (id={user.id}){'' if user.is_active else '  [معطّل]'}:")
            if not visible:
                print("   لا يرى أي حساب.")
                problems.append(f"المستخدم {user.username} لا يرى أي حساب.")
            for account in visible:
                _print_account(session, account, user)

        print("\n" + "=" * 72)
        print("هوية هذه العملية (من ملف .env)")
        print("=" * 72)
        print(f"MT5_ACCOUNT / MT5_SERVER : {settings.mt5_account} / {settings.mt5_server}")
        print(f"MT5_SYMBOL               : {settings.mt5_symbol}")

        terminal = settings.mt5_terminal_path
        if not terminal:
            print(f"{WARN} MT5_TERMINAL_PATH فارغ — مع أكثر من محطة مثبّتة قد يتصل بالخطأ منها.")
        elif not Path(terminal).exists():
            print(f"{BAD} MT5_TERMINAL_PATH لا يشير إلى ملف موجود: {terminal}")
            problems.append("MT5_TERMINAL_PATH خاطئ.")
        else:
            print(f"{OK} MT5_TERMINAL_PATH موجود: {terminal}")

        own = service.resolve_own_process_account(settings.mt5_account, settings.mt5_server)
        if own is None:
            print(f"{BAD} لا يوجد صف حساب يطابق هذه القيم — هذه العملية لن تنفّذ أي صفقة.")
            print("      المطابقة حرفية وحسّاسة لحالة الأحرف؛ أضف الحساب من اللوحة بنفس الصيغة.")
            problems.append("لا يوجد حساب مطابق لـ .env.")
        else:
            print(f"{OK} يطابق الحساب [id={own.id}] {own.label}")
            blind = [
                u.username
                for u in users
                if u.is_active
                and own.id not in {a.id for a in service.list_for_user(u.id)}  # type: ignore[arg-type]
            ]
            if blind:
                print(
                    f'{WARN} هذا الحساب لا يظهر لـ: {", ".join(blind)} — '
                    'فعّل مفتاح "حساب مشترك" من اللوحة ليراه الجميع.'
                )
                problems.append(f'الحساب المتداول لا يظهر لـ {", ".join(blind)}.')
            if not is_account_connected(own):
                stale = int(HEARTBEAT_STALE_AFTER.total_seconds())
                print(f"{BAD} هذا الحساب بلا نبض أحدث من {stale} ث — لا عملية متصلة به الآن.")
                problems.append("الحساب المتداول غير متصل.")

    print("\n" + "=" * 72)
    print("الوسيط الفعّال (الفحص الوحيد الذي يكشف السقوط على الوسيط الورقي)")
    print("=" * 72)
    marker, line = _broker_health()
    print(f"{marker} {line}")
    if marker != OK:
        problems.append("الوسيط الفعّال ليس MT5 حقيقيًا مؤكَّدًا.")

    print("\n" + "=" * 72)
    if problems:
        print("النتيجة: غير جاهز بعد —")
        for problem in problems:
            print(f"   - {problem}")
    else:
        print("النتيجة: جاهز. الحساب متصل بمحطة MT5 حقيقية، ويظهر لكل المستخدمين النشطين.")
    print("=" * 72)


if __name__ == "__main__":
    main()
