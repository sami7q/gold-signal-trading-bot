"use client";

import { CopyStatusBadge } from "@/components/ui/badge";
import { ErrorState, LoadingState } from "@/components/ui/empty-state";
import { Banner, DetailFrame, Fact, Section, leaderOrderLine, ltr } from "@/components/trade/detail-parts";
import { useCopyDetail } from "@/hooks/use-loco";
import {
  arCopyStatus,
  arCopyStatusExplain,
  arDecision,
  arLogType,
  arOrderKind,
  arOrderReason,
  arSide,
  cn,
  formatBps,
  formatClock,
  formatDateTime,
  formatDuration,
  formatMoney,
  formatMs,
  formatNumber,
  formatPrice,
  formatSignedPercent,
  formatUsd,
  leaderName,
  payloadSummary,
  pnlTone,
} from "@/lib/format";
import type { CopyDetail, TimelineItem } from "@/types/api";

const WARN_STATUSES = new Set(["CLOSED_RECONCILED", "CLOSED_LIQ", "CLOSED_MANUAL", "FAILED", "UNKNOWN"]);
const SLOW_EXIT_MS = 5_000;

type Tone = "leader" | "ours" | "warn" | "loss" | "done";

const DOT: Record<Tone, string> = {
  leader: "bg-gold",
  ours: "bg-bone",
  warn: "bg-warn",
  loss: "bg-loss",
  done: "bg-win",
};

function describe(item: TimelineItem, coin: string, who: string): { title: string; detail?: string; tone: Tone } {
  switch (item.kind) {
    case "LEADER_OPEN":
      return {
        title: item.order ? `${who} فتح صفقة ${arSide(item.order.side === "BUY" ? "LONG" : "SHORT")}` : `${who} فتح صفقة`,
        detail: leaderOrderLine(item.order, coin),
        tone: "leader",
      };
    case "DETECTED":
      return { title: "البوت التقط الحركة", detail: `بعد ${formatMs(item.lag_ms)} من صفقة ${who}`, tone: "ours" };
    case "ORDER_SENT":
      return {
        title: `أُرسل أمر ${arOrderKind(item.order.kind)}`,
        detail: `${formatNumber(item.order.requested_qty, 8)} ${coin} · ${arOrderReason(item.order.reason)}`,
        tone: "ours",
      };
    case "ORDER_FILLED":
      return {
        title: `تنفّذ أمر ${arOrderKind(item.order.kind)}`,
        detail: `${formatNumber(item.order.filled_qty, 8)} بسعر ${formatPrice(item.order.avg_price)} · رسوم ${ltr(formatUsd(item.order.fee))} · خلال ${formatMs(item.order.latency_ms)}`,
        tone: "ours",
      };
    case "ORDER_FAILED":
      return { title: `فشل أمر ${arOrderKind(item.order.kind)}`, detail: item.order.error ?? undefined, tone: "loss" };
    case "TP_SET":
      return {
        title: "وُضع هدف الربح على الوسيط",
        detail: `${formatPrice(item.px)} (${ltr(formatSignedPercent(item.move_pct))} من دخولنا)`,
        tone: "ours",
      };
    case "LEADER_ADD":
    case "LEADER_REDUCE":
    case "LEADER_CLOSE":
    case "LEADER_FLIP": {
      const e = item.event;
      const what = { LEADER_ADD: "عزّز", LEADER_REDUCE: "قلّص", LEADER_CLOSE: "أغلق", LEADER_FLIP: "عكس الاتجاه" }[item.kind];
      const fraction = e.fraction ? ` ${formatNumber(Number(e.fraction) * 100, 1)}%` : "";
      return {
        title: `${who} ${what}${fraction} بسعر ${formatPrice(e.leader_px)}`,
        detail: `التقطه البوت بعد ${formatMs(e.lag_ms)} · ${arDecision(e.decision)}${e.detail ? ` · ${e.detail}` : ""}`,
        tone: "leader",
      };
    }
    case "LEADER_EXIT_MISSED":
      return {
        title: `${who} أغلق مركزه — لم يلتقطه البوت لحظتها`,
        detail: leaderOrderLine(item.order, coin),
        tone: "warn",
      };
    case "LOG":
      return {
        title: arLogType(item.type),
        detail: payloadSummary(item.payload),
        tone: item.level === "WARNING" ? "warn" : "loss",
      };
    case "CLOSED":
      return { title: "أُغلقت الصفقة", detail: arCopyStatus(item.status), tone: WARN_STATUSES.has(item.status) ? "warn" : "done" };
  }
}

function Timeline({ d }: { d: CopyDetail }) {
  const first = d.timeline[0]?.at_ms ?? 0;
  if (!d.timeline.length) return <p className="text-xs text-muted-dim">لا توجد خطوات مسجّلة.</p>;
  return (
    <ol className="relative ms-1.5 border-s border-border-soft">
      {d.timeline.map((item, i) => {
        const t = describe(item, d.copy.coin, leaderName(d.copy.leader));
        return (
          <li key={`${item.kind}-${i}`} className="relative grid grid-cols-[6.5rem_1fr] gap-x-3 py-1.5 ps-4 sm:grid-cols-[6.5rem_4.5rem_1fr]">
            <span className={cn("absolute -start-[5px] top-[0.7rem] h-2.5 w-2.5 rounded-full ring-2 ring-ink", DOT[t.tone])} />
            <span dir="ltr" className="text-end font-data text-xs text-muted">{formatClock(item.at)}</span>
            <span dir={/[؀-ۿ]/.test(formatDuration(item.at_ms - first)) ? "rtl" : "ltr"} className="hidden text-end font-data text-[11px] text-muted-dim sm:block">
              {formatDuration(item.at_ms - first)}
            </span>
            <span className="col-span-2 min-w-0 sm:col-span-1">
              <span className={cn("text-sm", t.tone === "warn" ? "text-warn" : t.tone === "loss" ? "text-loss-bright" : "text-bone")}>
                {t.title}
              </span>
              {t.detail ? <span className="block break-words text-xs text-muted">{t.detail}</span> : null}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function Orders({ d }: { d: CopyDetail }) {
  if (!d.orders.length) return <p className="text-xs text-muted-dim">لم يُرسل أي أمر لهذه الصفقة.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] text-start text-xs">
        <thead>
          <tr className="border-b border-border-soft text-[11px] text-muted-dim">
            <th className="px-2 py-1.5 font-medium">الأمر</th>
            <th className="px-2 py-1.5 font-medium">السبب</th>
            <th className="px-2 py-1.5 text-end font-medium">الكمية</th>
            <th className="px-2 py-1.5 text-end font-medium">السعر</th>
            <th className="px-2 py-1.5 text-end font-medium">الرسوم</th>
            <th className="px-2 py-1.5 font-medium">أُرسل</th>
            <th className="px-2 py-1.5 font-medium">تنفّذ</th>
            <th className="px-2 py-1.5 text-end font-medium">المدة</th>
            <th className="px-2 py-1.5 font-medium">الحالة</th>
          </tr>
        </thead>
        <tbody className="font-data">
          {d.orders.map((o) => (
            <tr key={o.id} className="border-b border-border-soft/60 last:border-0">
              <td className="px-2 py-1.5 font-sans text-bone">{arOrderKind(o.kind)}</td>
              <td className="px-2 py-1.5 font-sans text-muted">{arOrderReason(o.reason)}</td>
              <td dir="ltr" className="px-2 py-1.5 text-end text-bone">{formatNumber(o.filled_qty ?? o.requested_qty, 8)}</td>
              <td dir="ltr" className="px-2 py-1.5 text-end text-bone">{formatPrice(o.avg_price)}</td>
              <td dir="ltr" className="px-2 py-1.5 text-end text-muted">{formatUsd(o.fee)}</td>
              <td dir="ltr" className="px-2 py-1.5 text-muted">{formatClock(o.sent_at)}</td>
              <td dir="ltr" className="px-2 py-1.5 text-muted">{formatClock(o.filled_at)}</td>
              <td dir="ltr" className="px-2 py-1.5 text-end text-muted">{formatMs(o.latency_ms)}</td>
              <td className={cn("px-2 py-1.5 font-sans", o.status === "FILLED" ? "text-win-bright" : o.status === "FAILED" ? "text-loss-bright" : "text-muted")}>
                {o.status === "FILLED" ? "منفّذ" : o.status === "FAILED" ? `فشل${o.error ? `: ${o.error}` : ""}` : o.status}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function TradeDetail({ id }: { id: number }) {
  const query = useCopyDetail(id);
  if (query.isLoading) return <DetailFrame><LoadingState /></DetailFrame>;
  if (query.isError || !query.data) {
    return <DetailFrame><ErrorState message={(query.error as Error | null)?.message ?? "—"} /></DetailFrame>;
  }

  const d = query.data;
  const c = d.copy;
  const f = d.figures;
  const cmp = d.comparison;
  const leader = d.leader;
  const isOpen = !c.closed_at;
  const slowExit = (cmp.exit_delay_ms ?? 0) > SLOW_EXIT_MS;
  const virtual = c.status === "OBSERVED_ONLY";
  const totalLatency = (c.latency_detect_ms ?? 0) + (c.latency_order_ms ?? 0);
  const who = leaderName(c.leader);

  return (
    <DetailFrame>
      <div className="flex flex-col gap-4">
      <Banner warn={WARN_STATUSES.has(c.status) || slowExit}>
        <CopyStatusBadge status={c.status} />
        <p className="min-w-0 flex-1">
          {arCopyStatusExplain(c.status)}
          {c.status === "FAILED" && c.close_reason ? ` (${c.close_reason})` : ""}
          {slowExit ? ` خرجنا بعد ${who} بـ ${formatDuration(cmp.exit_delay_ms)}.` : ""}
        </p>
      </Banner>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
        <Section title={`${who} مقابلنا`}>
          <Fact label={`دخول ${who}`} value={formatPrice(leader.open?.avg_px ?? c.leader_entry_px)} hint={leaderOrderLine(leader.open, c.coin)} />
          <Fact label="دخولنا" value={formatPrice(c.entry_price)} hint={`انزلاق ${ltr(formatBps(cmp.entry_diff_bps))}`} />
          <Fact
            label={`خروج ${who}`}
            value={leader.exit ? formatPrice(leader.exit.avg_px) : "لم يغلق بعد"}
            hint={leader.exit ? `${ltr(formatDateTime(leader.exit_time))}${leader.exit_captured ? "" : " · لم يلتقطه البوت لحظتها"}` : undefined}
            tone={leader.exit && !leader.exit_captured ? "warn" : "default"}
          />
          <Fact label="خروجنا" value={formatPrice(c.exit_price_avg)} hint={c.exit_price_avg ? `انزلاق ${ltr(formatBps(cmp.exit_diff_bps))}` : undefined} />
          <Fact label={`تأخر خروجنا عن ${who}`} value={formatDuration(cmp.exit_delay_ms)} tone={slowExit ? "warn" : "default"} />
        </Section>

        <Section title="الحجم والمخاطرة">
          <Fact label="الكمية" value={`${formatNumber(c.qty_initial, 8)} ${c.coin}`} hint={arSide(c.side)} />
          <Fact label="الحجم الكلي" value={formatUsd(f.notional)} />
          <Fact label="الهامش" value={formatUsd(f.margin)} hint={`رافعة ${f.leverage}x معزولة`} />
          <Fact label="هدف الربح" value={formatPrice(c.tp_price)} hint={`${ltr(formatSignedPercent(f.tp_move_pct))} من دخولنا`} />
          <Fact label="التصفية التقريبية" value={formatPrice(f.liq_price_est)} />
        </Section>

        <Section title={virtual ? "النتيجة (افتراضية)" : "النتيجة"}>
          <Fact label="حركة السعر لصالحنا" value={formatSignedPercent(f.price_move_pct, 3)} tone={pnlTone(f.price_move_pct)} />
          <Fact label="الربح قبل الرسوم" value={formatMoney(f.gross_pnl)} tone={pnlTone(f.gross_pnl)} />
          <Fact label="الرسوم (فتح + إغلاق)" value={formatMoney(-Number(f.fees))} />
          {Number(f.funding) !== 0 ? <Fact label="التمويل" value={formatMoney(f.funding)} /> : null}
          <Fact
            label="الصافي"
            value={isOpen ? "—" : formatMoney(f.net_pnl)}
            hint={isOpen ? "يُحسب عند الإغلاق" : `${ltr(formatSignedPercent(f.roi_pct))} على الهامش`}
            tone={isOpen ? "default" : pnlTone(f.net_pnl)}
          />
          <Fact label="المدة" value={formatDuration(f.duration_ms)} hint={isOpen ? "ما زالت مفتوحة" : undefined} />
        </Section>

        <Section title="السرعة">
          <Fact label={`من صفقة ${who} إلى البوت`} value={formatMs(c.latency_detect_ms)} />
          <Fact label="من الالتقاط إلى التنفيذ" value={formatMs(c.latency_order_ms)} />
          <Fact label="المجموع حتى دخولنا" value={formatMs(totalLatency || null)} tone="strong" />
          <Fact label="الأوامر المرسلة" value={d.orders.length} />
        </Section>
      </div>

      <div className="flex flex-col gap-3">
        <Section title="خط سير الصفقة خطوة بخطوة">
          <div className="py-2">
            <Timeline d={d} />
          </div>
        </Section>
        <Section title="الأوامر على الوسيط">
          <div className="py-2">
            <Orders d={d} />
          </div>
        </Section>
      </div>
      </div>
    </DetailFrame>
  );
}
