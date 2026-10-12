"use client";

import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { CopyStatusBadge, DecisionBadge } from "@/components/ui/badge";
import { ErrorState, LoadingState } from "@/components/ui/empty-state";
import { Banner, DetailFrame, Fact, Section, ltr, positionChange } from "@/components/trade/detail-parts";
import { useEventDetail } from "@/hooks/use-loco";
import {
  arEventType,
  arRejection,
  formatBps,
  formatClock,
  formatDateTime,
  formatMoney,
  formatMs,
  formatNumber,
  formatPrice,
  formatUsd,
  leaderName,
  pnlTone,
} from "@/lib/format";
import type { EventDetail as EventDetailData } from "@/types/api";

const WARN_DECISIONS = new Set(["REJECTED", "FAILED"]);

function explain(d: EventDetailData): string {
  const e = d.event;
  const who = leaderName(e.leader);
  switch (e.decision) {
    case "COPIED":
      return "نسخ البوت هذه الحركة وفتح صفقة مقابلة على الوسيط.";
    case "FOLLOWED":
      return e.type === "REDUCE" ? `قلّص البوت صفقتنا بنفس نسبة ${who}.` : `أغلق البوت صفقتنا مع ${who}.`;
    case "OBSERVED_ONLY":
      return "سجّلها البوت كصفقة افتراضية (وضع المحاكاة) — لم يُرسل أي أمر.";
    case "REJECTED":
      return `لم ينسخها البوت: ${e.rejection ? arRejection(e.rejection) : "سبب غير محدد"}.`;
    case "IGNORED_ADD":
      return `تعزيزات ${who} لا تُنسخ حسب الإعدادات — صفقتنا بقيت كما هي.`;
    case "IGNORED_REDUCE":
      return "متابعة التقليص معطّلة في الإعدادات — صفقتنا بقيت كما هي.";
    case "NO_MATCHING_COPY":
      return `لا توجد لنا صفقة مفتوحة على هذه الحركة (مركز سابق لـ ${who}، أو صفقة لم تُفتح أو أُغلقت قبله).`;
    case "FAILED":
      return `حاول البوت التنفيذ وفشل${e.detail ? `: ${e.detail}` : "."}`;
    case "ADDED":
      return `عزّز ${who} مركزه فعزّزنا صفقتنا بنفس النسبة R (ضمن سقف الهامش).`;
    case "ADD_PENDING":
      return "التعزيز أصغر من الحد الأدنى للأمر في Bybit، فأُجّل ويُضاف إلى التعزيز التالي.";
    case "REDUCED":
      return `قلّص ${who} مركزه فقلّصنا صفقتنا بنفس النسبة${e.detail?.includes("minimum") ? " (الكمية أصغر من الحد الأدنى — تُصحَّح في المطابقة)" : ""}.`;
    case "CLOSED":
      return `أغلق ${who} مركزه${e.type === "FLIP" ? " وعكس اتجاهه" : ""} فأغلقنا صفقتنا 100%.`;
    case "ADD_CAPPED":
      return "بلغت صفقتنا سقف الهامش (20% من الرصيد) — لا تعزيزات بعد الآن، والتقليص والإغلاق يُتابَعان كالعادة.";
  }
}

export function EventDetail({ id }: { id: number }) {
  const query = useEventDetail(id);
  if (query.isLoading) return <DetailFrame><LoadingState /></DetailFrame>;
  if (query.isError || !query.data) {
    return <DetailFrame><ErrorState message={(query.error as Error | null)?.message ?? "—"} /></DetailFrame>;
  }

  const d = query.data;
  const e = d.event;
  const order = d.leader_order;
  const who = leaderName(e.leader);

  return (
    <DetailFrame>
      <div className="flex flex-col gap-4">
      <Banner warn={WARN_DECISIONS.has(e.decision)}>
        <DecisionBadge decision={e.decision} />
        <p className="min-w-0 flex-1">{explain(d)}</p>
      </Banner>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        <Section title={`حركة ${who}: ${arEventType(e.type)} ${e.coin}`}>
          <Fact label={`وقت صفقة ${who}`} value={formatClock(e.leader_time)} hint={ltr(formatDateTime(e.leader_time))} />
          <Fact label="وصلت للبوت" value={formatClock(e.detected_at)} hint={`بعد ${formatMs(e.lag_ms)}`} />
          <Fact label="السعر" value={formatPrice(e.leader_px)} />
          {e.fraction ? <Fact label="نسبة التقليص" value={`${formatNumber(Number(e.fraction) * 100, 1)}%`} /> : null}
          <Fact label={`رقم الصفقة عند ${who}`} value={`#${e.episode_id}`} hint={`كل فتح جديد لـ ${who} على العملة يأخذ رقمًا جديدًا`} />
        </Section>

        <Section title={`أمر ${who} كاملًا`}>
          <Fact label="الحجم" value={order ? `${formatNumber(order.size, 8)} ${e.coin}` : "—"} hint={order && order.fills > 1 ? `نُفّذ على ${order.fills} قطعة في نفس اللحظة` : undefined} />
          <Fact label="متوسط السعر" value={formatPrice(order?.avg_px)} />
          <Fact label="القيمة" value={formatUsd(order?.notional)} />
          <Fact label={`مركز ${who} قبل وبعد`} value={positionChange(order)} hint="بالسالب = بيع (Short)" />
          {d.fill ? <Fact label="رقم الأمر على Hyperliquid" value={d.fill.oid ?? "—"} hint={`أول قطعة: ${d.fill.tid}`} /> : null}
        </Section>

        <Section title="صفقتنا المقابلة">
          {d.copy ? (
            <>
              <Fact label="الصفقة" value={`#${d.copy.id} · ${d.copy.symbol}`} hint={<CopyStatusBadge status={d.copy.status} />} />
              <Fact
                label="دخولنا"
                value={formatPrice(d.copy.entry_price)}
                hint={d.copy.slippage_bps !== null ? `انزلاق ${ltr(formatBps(Number(d.copy.slippage_bps)))}` : undefined}
              />
              <Fact label="الكمية" value={formatNumber(d.copy.qty_initial, 8)} />
              <Fact
                label="الصافي"
                value={d.copy.closed_at ? formatMoney(d.copy.realized_pnl) : "مفتوحة"}
                tone={d.copy.closed_at ? pnlTone(d.copy.realized_pnl) : "default"}
              />
              <div className="py-2">
                <Link
                  href={`/trades?open=${d.copy.id}`}
                  className="inline-flex items-center gap-1.5 rounded border border-gold-dim/60 px-2.5 py-1 text-xs text-gold-bright transition-colors hover:bg-gold/10"
                >
                  <ArrowLeft className="h-3.5 w-3.5" />
                  كل تفاصيل الصفقة
                </Link>
              </div>
            </>
          ) : (
            <p className="py-3 text-xs text-muted-dim">لا توجد لنا صفقة على هذه الحركة.</p>
          )}
        </Section>
      </div>
      </div>
    </DetailFrame>
  );
}
