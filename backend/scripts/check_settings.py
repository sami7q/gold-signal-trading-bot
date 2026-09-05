"""Read-only audit of one account's trading settings: every value, what
the pip-based ones are worth in dollars, the lot the very next signal
would actually use, and the combinations that quietly stop trades
(2026-09-05).

The dashboard shows each setting on its own. What it cannot show is how
they interact -- that a required Stop Loss with the fallback disabled
drops every signal the channel never sends an SL for, that a max of one
open trade makes the daily plan's second trade unreachable until the first
closes, or that the profit target is smaller than the widest Stop Loss
still accepted. Those are what actually decide whether a signal becomes a
position, and each one below is read from the same modules the engine uses
(app/trading/daily_lot_plan.py, loss_recovery.py, engine.py) rather than
re-derived here, so this can never drift from what would really happen.

Touches nothing: plain SELECTs, and it never calls
`SettingsService.get_for_account`, which would create a settings row as a
side effect of looking.

Usage (from the backend/ directory, with the project venv active):

    python scripts/check_settings.py                  # this .env's own account
    python scripts/check_settings.py 50071008         # a specific account
    python scripts/check_settings.py 111715721@MetaQuotes-Demo
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]

from sqlmodel import Session, select  # noqa: E402

from app.accounts.service import AccountService  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.decimals import fmt  # noqa: E402
from app.database.models.mt5_account import MT5Account  # noqa: E402
from app.database.models.trading_settings import TradingSettings  # noqa: E402
from app.database.session import get_engine  # noqa: E402
from app.trading.daily_lot_plan import next_trade_lot, trading_day  # noqa: E402
from app.trading.engine import last_trade, todays_trades  # noqa: E402
from app.trading.loss_recovery import next_trade_lot as next_recovery_lot  # noqa: E402

OK, WARN = "[ OK ]", "[ !! ]"

#: What one pip is worth per lot, under each of the two ways brokers quote
#: Gold. app/trading/pips.py derives this from the broker's own metadata at
#: runtime; here there is no broker connection, so both are shown and the
#: operator matches the one their broker uses (Ctrl+U in the terminal ->
#: XAUUSD -> Digits, or the pip preview on the dashboard's settings page).
_PIP_2DIGIT = Decimal("0.10")
_PIP_3DIGIT = Decimal("0.01")
#: XAUUSD is quoted per ounce and one lot is 100 ounces on every broker
#: this bot has met -- so a pip of price movement is worth 100x that per lot.
_CONTRACT_SIZE = Decimal("100")


def _money_row(label: str, pips: Decimal | None, lot: Decimal) -> str:
    """One row of the pips-in-dollars table: how far the price must travel,
    and what that is worth on `lot` lots -- each under both quoting
    conventions, since this script has no broker to ask which applies."""
    if pips is None:
        return f"  {label:<22}  {'معطّل':>7}"
    move2, move3 = pips * _PIP_2DIGIT, pips * _PIP_3DIGIT
    pnl2, pnl3 = move2 * _CONTRACT_SIZE * lot, move3 * _CONTRACT_SIZE * lot
    return (
        f"  {label:<22}  {fmt(pips):>7}   "
        f"${move2:,.2f} / ${move3:,.2f}".ljust(24)
        + f"   ${pnl2:,.2f} / ${pnl3:,.2f}"
    )


def _yes(value: bool) -> str:
    return "نعم" if value else "لا"


def _resolve(session: Session, spec: str | None) -> MT5Account:
    service = AccountService(session)
    if spec is None:
        env = get_settings()
        account = service.resolve_own_process_account(env.mt5_account, env.mt5_server)
        if account is None:
            print(
                f"لا يوجد حساب يطابق .env ({env.mt5_account}@{env.mt5_server}). "
                "مرّر رقم الحساب كوسيط لفحص حساب آخر."
            )
            raise SystemExit(1)
        return account

    login, _, server = spec.partition("@")
    matches = service.find_by_login(login.strip(), server.strip() or None)
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


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] in {"-h", "--help"}:
        print(__doc__)
        raise SystemExit(0)

    env = get_settings()
    warnings: list[str] = []

    with Session(get_engine()) as session:
        account = _resolve(session, args[0] if args else None)
        own = AccountService(session).resolve_own_process_account(
            env.mt5_account, env.mt5_server
        )
        audits_own_account = own is not None and own.id == account.id
        assert account.id is not None
        settings = session.exec(
            select(TradingSettings).where(TradingSettings.account_id == account.id)
        ).first()

        print("=" * 72)
        print(f"إعدادات الحساب [id={account.id}] {account.label}")
        print(f"{account.mt5_login}@{account.mt5_server} | {account.symbol} | "
              f"رصيد مسجّل: {fmt(account.last_balance)}")
        print("=" * 72)

        if settings is None:
            print("لا صف إعدادات لهذا الحساب بعد — سيبدأ بالقيم الافتراضية")
            print("(التداول التلقائي مطفأ)، فلن ينفّذ شيئًا حتى تضبطه من اللوحة.")
            raise SystemExit(1)

        print("\n-- التنفيذ --")
        print(f"  التداول التلقائي        : {'مفعّل' if settings.auto_trading else 'مطفأ'}")
        print(f"  إشارات الشراء           : {'مفعّلة' if settings.buy_enabled else 'مطفأة'}")
        print(f"  إشارات البيع            : {'مفعّلة' if settings.sell_enabled else 'مطفأة'}")
        print(f"  أقصى صفقات مفتوحة معًا  : {settings.max_open_trades}")
        print(f"  الوضع المسجَّل (mode)     : {settings.mode}")
        if audits_own_account:
            print(f"      (لا يمنع التنفيذ — البوابة الفعلية هي TRADING_MODE={env.trading_mode} "
                  "في .env هذه العملية، وهي ما يفحصه الوسيط عند الاتصال)")
        else:
            # Another account is driven by a different process with its own
            # env file (a worker's GOLDBOT_ENV_FILE), so this process's
            # TRADING_MODE says nothing about it -- printing it here would
            # invite reading a demo hub's setting as that account's.
            print("      (لا يمنع التنفيذ — البوابة الفعلية هي TRADING_MODE في ملف .env الخاص "
                  "بالعملية التي تدير هذا الحساب، وهو ملف آخر لا تقرأه هذه العملية)")

        print("\n-- الهدف والانزلاق --")
        print(f"  حجم اللوت الأساسي       : {fmt(settings.lot_size)}")
        print(f"  الهدف                   : {fmt(settings.target_pips)} نقطة")
        if audits_own_account:
            print(f"  تسامح الانزلاق (.env)   : {fmt(env.mt5_max_slippage_pips)} نقطة")
        else:
            print("  تسامح الانزلاق          : من ملف .env الخاص بعملية هذا الحساب (غير مقروء هنا)")

        print("\n-- وقف الخسارة --")
        print(f"  إلزامي من القناة        : {_yes(settings.require_stop_loss)}")
        print(f"  أقصى مسافة لستوب القناة : {fmt(settings.max_channel_stop_pips)} نقطة")
        print(
            "  الوقف الاحتياطي التلقائي: "
            + (f"{fmt(settings.auto_sl_fallback_pips)} نقطة" if settings.auto_sl_fallback_pips
               else "معطّل")
        )
        print(f"  مهلة اكتمال الإشارة     : {settings.signal_completion_timeout_seconds} ثانية")

        print("\n-- خطة اللوت اليومية --")
        if settings.daily_lot_plan_enabled:
            print(f"  مفعّلة | أقصى صفقات باليوم: {settings.max_trades_per_day}")
            print(f"  لوت الاثنين الأولى      : {fmt(settings.monday_first_trade_lot)}")
            print(f"  لوت الاثنين الثانية     : {fmt(settings.monday_second_trade_lot)}")
            print(f"  مضاعف بعد الخسارة       : {fmt(settings.martingale_lot_multiplier)} "
                  f"(= {fmt(settings.lot_size * settings.martingale_lot_multiplier)} لوت)")
        else:
            print("  معطّلة — كل صفقة تستخدم اللوت الأساسي، بلا حد يومي.")

        print("\n-- استرداد الخسارة --")
        print(f"  {'مفعّل' if settings.loss_recovery_enabled else 'معطّل'}")

        print("\n-- لو وصلت إشارة الآن --")
        now = datetime.now(UTC)
        next_lot = settings.lot_size
        today = todays_trades(session, now, account.id)
        print(f"  يوم التداول (بغداد)     : {trading_day(now)}")
        print(f"  صفقات نُفِّذت اليوم       : {len(today)}")
        if settings.daily_lot_plan_enabled:
            plan = next_trade_lot(today, now=now, settings=settings)
            if plan.allowed:
                print(f"  اللوت المستخدم          : {fmt(plan.lot)}  ({plan.explanation})")
                next_lot = plan.lot or settings.lot_size
            else:
                print(f"  {WARN} لن تُنفَّذ: {plan.explanation}")
        elif settings.loss_recovery_enabled:
            recovery = next_recovery_lot(last_trade(session, account.id), settings=settings)
            print(f"  اللوت المستخدم          : {fmt(recovery.lot)}  ({recovery.explanation})")
            next_lot = recovery.lot
        else:
            print(f"  اللوت المستخدم          : {fmt(settings.lot_size)} (بلا خطة)")

        print(f"\n-- ماذا تعني نقاطك بالدولار (عند لوت {fmt(next_lot)}) --")
        print(f"  {'':<22}  {'النقاط':>7}   {'مسافة السعر':<21}   الربح/الخسارة")
        print(_money_row("الهدف", settings.target_pips, next_lot))
        print(_money_row("أقصى ستوب القناة", settings.max_channel_stop_pips, next_lot))
        print(_money_row("الوقف الاحتياطي", settings.auto_sl_fallback_pips, next_lot))
        if audits_own_account:
            print(_money_row("تسامح الانزلاق", env.mt5_max_slippage_pips, next_lot))
        print("  كل قيمة معروضة: ذهب بخانتين / بثلاث خانات")

        # -- combinations that quietly stop or distort trading -----------
        if not settings.auto_trading:
            warnings.append("التداول التلقائي مطفأ — لن تُفتح أي صفقة مهما كانت باقي الإعدادات.")
        if not settings.buy_enabled and not settings.sell_enabled:
            warnings.append("الشراء والبيع كلاهما مطفأ — كل إشارة سترفض.")
        if settings.require_stop_loss and settings.auto_sl_fallback_pips is None:
            warnings.append(
                "وقف الخسارة إلزامي والوقف الاحتياطي معطّل — أي إشارة لا ترسل القناة لها "
                "وقف خسارة خلال المهلة تسقط دون تنفيذ."
            )
        if settings.daily_lot_plan_enabled and settings.loss_recovery_enabled:
            warnings.append(
                "الخطة اليومية واسترداد الخسارة مفعّلتان معًا — اليومية وحدها هي التي تعمل "
                "(app/trading/engine.py)، فالثانية مطفأة عمليًا."
            )
        if (
            settings.max_channel_stop_pips is not None
            and settings.max_channel_stop_pips > settings.target_pips
        ):
            warnings.append(
                f"أقصى ستوب مقبول ({fmt(settings.max_channel_stop_pips)} نقطة) أكبر من الهدف "
                f"({fmt(settings.target_pips)} نقطة) — أي أن أسوأ خسارة مسموحة أكبر من أفضل ربح."
            )
        if (
            settings.daily_lot_plan_enabled
            and settings.max_trades_per_day > settings.max_open_trades
        ):
            warnings.append(
                f"الخطة تسمح بـ {settings.max_trades_per_day} صفقات يوميًا بينما أقصى صفقات "
                f"مفتوحة معًا {settings.max_open_trades} — الصفقة التالية لن تُنفَّذ إلا بعد "
                "إغلاق السابقة."
            )
        if settings.daily_lot_plan_enabled and account.last_balance:
            heaviest = max(
                settings.monday_first_trade_lot,
                settings.lot_size * settings.martingale_lot_multiplier,
            )
            warnings.append(
                f"أثقل لوت في الخطة {fmt(heaviest)} — تحقّق أنه ضمن حدود الوسيط والهامش "
                f"المتاح (الرصيد المسجّل {fmt(account.last_balance)}); لوت أكبر من طاقة "
                "الهامش يُرفض من الوسيط لحظة التنفيذ."
            )

    print("\n" + "=" * 72)
    if warnings:
        print("ملاحظات تستحق مراجعتك:")
        for warning in warnings:
            print(f"{WARN} {warning}")
    else:
        print(f"{OK} لا تعارض بين الإعدادات.")
    print("=" * 72)
    print("قيم النقاط أعلاه معروضة بالاحتمالين لأن هذا السكربت لا يتصل بالوسيط.")
    print("لمعرفة أيّهما ينطبق: صفحة الإعدادات في اللوحة تعرض معاينة النقاط من الوسيط الحي،")
    print("أو Ctrl+U في المحطة -> XAUUSD -> خانة Digits.")


if __name__ == "__main__":
    main()
