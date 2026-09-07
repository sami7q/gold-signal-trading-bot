"""Explain exactly why one trade never became a position (2026-09-05).

The trades table now names the rejection reason, but a code alone does not
say which of the conditions behind it fired, on which account, or what else
the bot was doing at that moment -- and the two accounts run in *separate
processes* against separate MT5 terminals, so "it worked on the other
account" says nothing about this one. This gathers the trade, the signal
behind it, the account that rejected it, and the audit trail from either
side of it, then spells out what that particular reason means.

Read-only: plain SELECTs, nothing is written.

Usage (from the backend/ directory, with the project venv active):

    python scripts/why_rejected.py <trade_id>
    python scripts/why_rejected.py            # the most recent rejected/failed trade
"""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]

from sqlmodel import Session, select  # noqa: E402

from app.core.constants import RejectionReason, TradeStatus  # noqa: E402
from app.core.decimals import fmt  # noqa: E402
from app.database.models.audit_event import AuditEvent  # noqa: E402
from app.database.models.mt5_account import MT5Account, is_account_connected  # noqa: E402
from app.database.models.signal import Signal  # noqa: E402
from app.database.models.trade import Trade  # noqa: E402
from app.database.session import get_engine  # noqa: E402

#: How far either side of the trade to pull the audit trail. Wide enough to
#: catch a process restart or a broker error logged just before the
#: rejection, narrow enough not to bury it.
_WINDOW = timedelta(minutes=10)

#: What each reason actually means, in the terms an operator can act on.
#: Keyed by RejectionReason; a FAILED trade carries the broker's own raw
#: text instead and gets the fallback note below.
_MEANING: dict[str, list[str]] = {
    RejectionReason.BROKER_UNAVAILABLE: [
        "محرّك القواعد لم يجد وسيطًا صالحًا لحظة التنفيذ. يفشل هذا الفحص إذا تحقّق أي واحد:",
        "  * فحص صحّة الوسيط رجع غير سليم (الاتصال بالمحطة مقطوع)،",
        "  * أو get_account() فشل (المحطة غير مسجّلة الدخول)،",
        "  * أو get_symbol('XAUUSD') فشل (الرمز غير موجود بهذا الاسم عند هذا الوسيط)،",
        "  * أو get_quote() فشل / بلا تسعيرة حيّة (السوق مغلق، أو الرمز غير مُفعّل في Market Watch).",
        "",
        "الأهم: كل حساب يعمل بعمليته ومحطته المستقلّتين. نجاح نفس الإشارة على الحساب الآخر",
        "لا يعني شيئًا عن هذا الحساب — افحص محطة هذا الحساب تحديدًا: هل هي مفتوحة، مسجّلة",
        "الدخول، متصلة (أسفل يمين المحطة)، وهل XAUUSD ظاهر في Market Watch؟",
    ],
    RejectionReason.STOP_LOSS_TOO_WIDE: [
        "وقف خسارة القناة كان أبعد من max_channel_stop_pips عن السعر لحظة الفحص.",
        "ارفع الحد من الإعدادات إن أردت قبول إشارات أوسع.",
    ],
    RejectionReason.INVALID_LOT_SIZE: [
        "حجم اللوت لا يقع على خطوة الوسيط، أو خارج حدّيه الأدنى/الأقصى.",
        "افحص Ctrl+U -> XAUUSD -> Volume min / Volume step في المحطة.",
    ],
    RejectionReason.MAX_OPEN_TRADES_REACHED: [
        "عدد الصفقات المفتوحة بلغ max_open_trades وقتها.",
    ],
    RejectionReason.DAILY_TRADE_LIMIT_REACHED: [
        "خطة اللوت اليومية منعت صفقة إضافية اليوم (max_trades_per_day).",
    ],
    RejectionReason.AUTO_TRADING_DISABLED: [
        "التداول التلقائي كان مطفأً لهذا الحساب وقتها.",
    ],
    RejectionReason.STOP_LOSS_REQUIRED: [
        "القناة لم ترسل وقف خسارة، و require_stop_loss مفعّل بلا وقف احتياطي.",
    ],
    RejectionReason.UNSUPPORTED_SIGNAL_TYPE: [
        "أمر معلّق (LIMIT/STOP) — البوت ينفّذ أوامر السوق (BUY/SELL) فقط.",
    ],
}


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] in {"-h", "--help"}:
        print(__doc__)
        raise SystemExit(0)

    with Session(get_engine()) as session:
        if args:
            try:
                trade = session.get(Trade, int(args[0]))
            except ValueError:
                print(f"{args[0]!r} ليس رقم صفقة.")
                raise SystemExit(1) from None
        else:
            trade = session.exec(
                select(Trade)
                .where(Trade.status.in_([TradeStatus.REJECTED, TradeStatus.FAILED]))  # type: ignore[attr-defined]
                .order_by(Trade.id.desc())  # type: ignore[union-attr]
            ).first()

        if trade is None:
            print("لا توجد صفقة بهذا الرقم." if args else "لا توجد صفقة مرفوضة أو فاشلة.")
            raise SystemExit(1)

        account = session.get(MT5Account, trade.account_id)
        signal = session.get(Signal, trade.signal_id)

        print("=" * 72)
        print(f"الصفقة #{trade.id} — {trade.status}")
        print("=" * 72)
        print(f"  الحساب      : [id={trade.account_id}] "
              f"{account.label if account else '(محذوف)'} "
              f"({account.mt5_login + '@' + account.mt5_server if account else ''})")
        print(f"  الاتجاه/الرمز: {trade.side} {trade.symbol}")
        print(f"  اللوت المطلوب: {fmt(trade.requested_lot)}")
        print(f"  وقف الخسارة  : {fmt(trade.stop_loss)}")
        print(f"  هدف النقاط   : {fmt(trade.target_pips)}")
        print(f"  الوقت (UTC)  : {trade.created_at}")
        print(f"  السبب        : {trade.failure_reason}")

        if signal is not None:
            print("\n-- الإشارة خلفها --")
            print(f"  الإشارة #{signal.id} | {signal.side} {signal.symbol} | {signal.signal_type}")
            print(f"  حالتها      : {signal.status}")
            print(f"  سعر القناة  : {fmt(signal.entry_min)} - {fmt(signal.entry_max)}")
            print(f"  ستوب القناة : {fmt(signal.channel_stop_loss)}")
            print(f"  أول رسالة   : {signal.first_message_at}")

            siblings = list(
                session.exec(select(Trade).where(Trade.signal_id == signal.id))
            )
            if len(siblings) > 1:
                print("\n-- نفس الإشارة على الحسابات الأخرى --")
                for other in siblings:
                    if other.id == trade.id:
                        continue
                    other_account = session.get(MT5Account, other.account_id)
                    label = other_account.label if other_account else "(محذوف)"
                    print(f"  #{other.id} [{label}] {other.status} "
                          f"{'— ' + other.failure_reason if other.failure_reason else ''}")

        if account is not None:
            state = "متصل الآن" if is_account_connected(account) else "غير متصل الآن"
            print(f"\n-- حالة هذا الحساب الآن (وليس وقت الرفض) --\n  {state} | "
                  f"آخر نبض {account.last_heartbeat_at} | رصيد {fmt(account.last_balance)}")

        window_start = trade.created_at - _WINDOW
        window_end = trade.created_at + _WINDOW
        events = list(
            session.exec(
                select(AuditEvent)
                .where(AuditEvent.created_at >= window_start)
                .where(AuditEvent.created_at <= window_end)
                .order_by(AuditEvent.created_at.asc())  # type: ignore[attr-defined]
            )
        )
        print(f"\n-- سجل التدقيق حول الرفض (±{int(_WINDOW.total_seconds() // 60)} دقيقة) --")
        if not events:
            print("  لا شيء — وهذا نفسه دليل: العملية لم تسجّل شيئًا آخر في تلك النافذة.")
        for event in events:
            meta = json.loads(event.metadata_json or "{}")
            account_note = f" [account_id={meta['account_id']}]" if "account_id" in meta else ""
            print(f"  {event.created_at} {event.severity:<8} {event.event_type}{account_note}")
            print(f"      {event.message}")

        print("\n" + "=" * 72)
        print("ماذا يعني هذا السبب")
        print("=" * 72)
        for line in _MEANING.get(
            trade.failure_reason or "",
            ["هذا نص خطأ جاء من الوسيط نفسه (صفقة FAILED)، لا رمز رفض داخلي — "
             "اقرأه حرفيًا أعلاه فهو رد MT5."],
        ):
            print(line)


if __name__ == "__main__":
    main()
