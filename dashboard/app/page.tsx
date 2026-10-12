"use client";

import Link from "next/link";
import { Activity, AlertTriangle, Inbox, Radar } from "lucide-react";
import { StatCard } from "@/components/ui/stat-card";
import { Card, CardHeader } from "@/components/ui/card";
import { CopyStatusBadge, DecisionBadge, EventTypeBadge, SideBadge } from "@/components/ui/badge";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/empty-state";
import { useCopies, useEvents, useLocoPositions, useOverview } from "@/hooks/use-loco";
import {
  arMode,
  arRejection,
  brokerLabel,
  formatDateTime,
  formatMoney,
  formatMs,
  formatNumber,
  formatPercent,
  formatPrice,
  formatRelativeTime,
  formatUsd,
  pnlClass,
  pnlTone,
} from "@/lib/format";
import type { Overview } from "@/types/api";

function Alerts({ data }: { data: Overview }) {
  const items: string[] = [];
  if (!data.bot.running) items.push("البوت متوقف على السيرفر — لا يتابع المتداولين الآن.");
  else if (!data.bot.ws_connected) items.push("الاتصال اللحظي بـ Hyperliquid منقطع — البوت يحاول إعادة الاتصال.");
  if (data.risk.kill_switch) items.push("الإيقاف الطارئ مفعّل — لا تُفتح صفقات جديدة (الإغلاقات مستمرة).");
  if (data.risk.leader_breaker) items.push("قاطع تراجع أحد المتداولين مفعّل — تراجع حسابه أكثر من الحد المسموح.");
  if (data.risk.daily_loss_hit) items.push("بلغ حد الخسارة اليومي — لا صفقات جديدة حتى بداية اليوم التالي (UTC).");
  if (data.account.account_kill) items.push("قاطع الحساب مفعّل: الرصيد نزل تحت 75% من قمته — لا صفقات أو تعزيزات جديدة حتى الاستئناف.");
  if (!items.length) return null;
  return (
    <div className="flex flex-col gap-1.5 rounded-md border border-loss/40 bg-loss/10 px-4 py-3">
      {items.map((t) => (
        <p key={t} className="flex items-center gap-2 text-sm text-loss-bright">
          <AlertTriangle className="h-4 w-4 shrink-0" strokeWidth={1.75} />
          {t}
        </p>
      ))}
    </div>
  );
}

export default function OverviewPage() {
  const overview = useOverview();
  const loco = useLocoPositions();
  const copies = useCopies(undefined, 8);
  const events = useEvents(undefined, 8);
  const data = overview.data;
  const stats = data?.stats;

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-bone">نظرة عامة</h1>
        <p className="mt-1 text-sm text-muted">
          نسخ صفقات المتداولين إلى {brokerLabel(data?.account.broker, data?.account.env)} —{" "}
          {data?.lanes?.length
            ? data.lanes.map((l, i) => (
                <span key={l.label}>
                  {i ? " · " : ""}
                  {l.name}: <span className="text-bone">{l.paused ? "موقوف" : arMode(l.mode)}</span>
                </span>
              ))
            : <span className="text-bone">{data ? arMode(data.mode.mode) : "—"}</span>}
          . الأرقام أدناه لكل المتداولين معًا.
        </p>
      </div>

      {overview.isLoading ? (
        <LoadingState />
      ) : overview.isError || !data || !stats ? (
        <ErrorState message={(overview.error as Error | null)?.message ?? "—"} />
      ) : (
        <>
          <Alerts data={data} />
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatCard label="الرصيد" value={formatUsd(data.account.equity)} tone="gold" sub={`المتاح ${formatUsd(data.account.available)}`} />
            <StatCard
              label="ربح/خسارة اليوم"
              value={formatMoney(stats.realized_pnl_today)}
              tone={pnlTone(stats.realized_pnl_today)}
              sub={`${stats.closed_today} صفقة أُغلقت اليوم`}
            />
            <StatCard label="المراكز المفتوحة" value={stats.open_copies} sub={`${data.leader.name ?? "Loco"} لديه ${data.leader.open_positions} مركز`} />
            <StatCard label="إجمالي الصفقات" value={stats.trades_total} sub={`${stats.take_profits} هدف ربح · ${stats.liquidations} تصفية`} />
            <StatCard label="الرابحة" value={stats.wins} tone="win" />
            <StatCard label="الخاسرة" value={stats.losses} tone="loss" />
            <StatCard label="نسبة الربح" value={formatPercent(stats.win_rate)} tone="gold" />
            <StatCard label="صافي الربح المحقق" value={formatMoney(stats.realized_pnl_total)} tone={pnlTone(stats.realized_pnl_total)} />
          </div>
          {data.strategy?.name === "cap20_cross" ? (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <StatCard
                label="الحجم المفتوح ÷ الرصيد"
                value={data.account.exposure_x ? `${formatNumber(data.account.exposure_x, 2)}x` : "—"}
                tone={Number(data.account.exposure_x ?? 0) > 4 ? "loss" : "default"}
                sub={`الحد ${formatNumber(data.strategy.max_total_notional_x_equity, 0)}x · ${formatUsd(data.account.notional)}`}
              />
              <StatCard
                label="البعد عن التصفية"
                value={data.account.liq_distance ? `${formatNumber(data.account.liq_distance, 1)}x` : "لا مراكز"}
                tone={data.account.liq_distance && Number(data.account.liq_distance) < 3 ? "loss" : "default"}
                sub="الرصيد ÷ هامش الصيانة (Bybit) — 1x = تصفية"
              />
              <StatCard
                label="الاستراتيجية"
                value={`سقف ${formatNumber(data.strategy.per_trade_margin_cap_pct, 0)}%`}
                tone="gold"
                sub={`هامش مشترك · رافعة ${data.strategy.leverage}x · K=${data.strategy.k}`}
              />
              <Link href="/trio" className="rounded-md border border-gold-dim/60 bg-gold/5 p-3.5 transition-colors hover:bg-gold/10">
                <div className="text-[11px] font-medium text-muted">الثلاثي — الحساب الافتراضي</div>
                <div className="mt-1.5 text-sm font-medium text-gold-bright">افتح لوحة المراقبة ←</div>
                <div className="mt-1.5 text-xs text-muted">الرصيد الافتراضي، السرعة، الانزلاق، وشروط الانتقال</div>
              </Link>
            </div>
          ) : null}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatCard label={`حساب ${data.leader.name ?? "Loco"}`} value={formatUsd(data.leader.account_value)} sub={`لقطة ${formatRelativeTime(data.leader.snapshot_at)}`} />
            <StatCard label="سرعة الالتقاط (24س)" value={formatMs(data.metrics_24h.detect_p50_ms)} sub="من صفقة المتداول حتى وصولها للبوت" />
            <StatCard label="سرعة التنفيذ (24س)" value={formatMs(data.metrics_24h.order_p50_ms)} sub="من الالتقاط حتى التنفيذ" />
            <StatCard
              label="صفقات المراقبة"
              value={stats.observed_total}
              sub={`ربح افتراضي ${formatMoney(stats.observed_pnl)}`}
            />
          </div>
        </>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader
            title="مراكز Loco الآن"
            subtitle="من آخر لقطة لحسابه"
            action={<Link href="/loco" className="text-xs text-gold hover:text-gold-bright">عرض الكل ←</Link>}
          />
          {loco.isLoading ? (
            <LoadingState />
          ) : !loco.data?.rows.length ? (
            <EmptyState icon={Radar} title="Loco ليس لديه مراكز مفتوحة" />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-border-soft text-[11px] text-muted-dim">
                    <th className="px-4 py-2 font-medium">العملة</th>
                    <th className="px-4 py-2 font-medium">الاتجاه</th>
                    <th className="px-4 py-2 text-right font-medium">الحجم</th>
                    <th className="px-4 py-2 text-right font-medium">دخول Loco</th>
                    <th className="px-4 py-2 font-medium">نحن</th>
                  </tr>
                </thead>
                <tbody className="font-data whitespace-nowrap">
                  {loco.data.rows.slice(0, 8).map((row) => (
                    <tr key={row.coin} className="border-b border-border-soft/60 last:border-0">
                      <td className="px-4 py-2 text-bone">{row.coin}</td>
                      <td className="px-4 py-2"><SideBadge side={row.side} /></td>
                      <td className="px-4 py-2 text-right text-bone">{formatNumber(row.size, 4)}</td>
                      <td className="px-4 py-2 text-right text-bone">{formatPrice(row.entry_px)}</td>
                      <td className="px-4 py-2 font-sans text-xs">
                        {row.ours ? <CopyStatusBadge status={row.ours.status} /> : <span className="text-muted-dim">{row.copyable ? "—" : "سابق — لا يُنسخ"}</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card>
          <CardHeader
            title="آخر حركات المتداولين"
            subtitle="وقرار البوت في كل حركة"
            action={<Link href="/events" className="text-xs text-gold hover:text-gold-bright">عرض الكل ←</Link>}
          />
          {events.isLoading ? (
            <LoadingState />
          ) : !events.data?.length ? (
            <EmptyState icon={Activity} title="لا توجد حركات بعد" description="كل حركة يقوم بها أي متداول تظهر هنا خلال ثوانٍ." />
          ) : (
            <ul className="divide-y divide-border-soft/60">
              {events.data.map((e) => (
                <li key={e.id} className="flex items-center justify-between gap-3 px-4 py-2.5">
                  <div className="flex min-w-0 items-center gap-2">
                    <EventTypeBadge type={e.type} />
                    <span className="font-data text-sm text-bone">{e.coin}</span>
                    <span className="truncate text-xs text-muted-dim">
                      {formatDateTime(e.detected_at)}
                      {e.rejection ? ` · ${arRejection(e.rejection)}` : ""}
                    </span>
                  </div>
                  <DecisionBadge decision={e.decision} />
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card>
        <CardHeader
          title="أحدث صفقاتنا"
          subtitle="الأحدث أولًا"
          action={<Link href="/trades" className="text-xs text-gold hover:text-gold-bright">عرض الكل ←</Link>}
        />
        {copies.isLoading ? (
          <LoadingState />
        ) : !copies.data?.length ? (
          <EmptyState icon={Inbox} title="لا توجد صفقات بعد" description="أول صفقة جديدة يفتحها أي متداول بعد تفعيل النسخ ستظهر هنا." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-sm">
              <thead>
                <tr className="border-b border-border-soft text-[11px] text-muted-dim">
                  <th className="px-4 py-2 font-medium">الفتح</th>
                  <th className="px-4 py-2 font-medium">العملة</th>
                  <th className="px-4 py-2 font-medium">الاتجاه</th>
                  <th className="px-4 py-2 text-right font-medium">دخولنا</th>
                  <th className="px-4 py-2 text-right font-medium">الهدف</th>
                  <th className="px-4 py-2 text-right font-medium">الربح</th>
                  <th className="px-4 py-2 font-medium">الحالة</th>
                </tr>
              </thead>
              <tbody className="font-data whitespace-nowrap">
                {copies.data.map((c) => (
                  <tr key={c.id} className="border-b border-border-soft/60 last:border-0">
                    <td className="px-4 py-2 text-muted">{formatDateTime(c.opened_at)}</td>
                    <td className="px-4 py-2 text-bone">{c.symbol}</td>
                    <td className="px-4 py-2"><SideBadge side={c.side} /></td>
                    <td className="px-4 py-2 text-right text-bone">{formatPrice(c.entry_price)}</td>
                    <td className="px-4 py-2 text-right text-win-bright">{formatPrice(c.tp_price)}</td>
                    <td className={`px-4 py-2 text-right ${pnlClass(c.realized_pnl)}`}>{c.closed_at ? formatMoney(c.realized_pnl) : "—"}</td>
                    <td className="px-4 py-2"><CopyStatusBadge status={c.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
