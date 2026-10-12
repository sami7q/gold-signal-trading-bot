"use client";

import { useState } from "react";
import Link from "next/link";
import { Area, AreaChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AlertTriangle, CheckCircle2, CircleDashed, Layers, XCircle } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/card";
import { StatCard } from "@/components/ui/stat-card";
import { DecisionBadge, EventTypeBadge, LeaderBadge, ModeBadge, SideBadge } from "@/components/ui/badge";
import { Label, Select } from "@/components/ui/input";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/empty-state";
import { useVirtual } from "@/hooks/use-loco";
import {
  cn,
  formatDateTime,
  formatMoney,
  formatMs,
  formatNumber,
  formatPercent,
  formatPrice,
  formatRatio,
  formatRelativeTime,
  formatUsd,
  pnlClass,
  pnlTone,
} from "@/lib/format";
import type { GoCheck, VirtualPanel } from "@/types/api";

const CHART_TEXT = "#8b8f98";
const GOLD = "#c9a227";

// botup.md v5 §V5.6 -- every check must pass before the trio may trade even on the Bybit demo.
const CHECKS: Record<GoCheck["key"], { label: string; target: string; show: (v: number | null) => string }> = {
  missed_exits: { label: "إغلاقات المتداول التي فاتت البوت", target: "0", show: (v) => String(v ?? 0) },
  latency_p95: { label: "زمن الالتقاط p95", target: "≤ 10 ث", show: (v) => formatMs(v) },
  median_slippage: {
    label: "وسيط الفرق عن سعر المتداول",
    target: "≤ 0.15%",
    show: (v) => (v === null ? "—" : `${formatNumber(v / 100, 3)}%`),
  },
  errors: { label: "أخطاء الاتصال والمطابقة", target: "لا شيء معلّق", show: (v) => String(v ?? 0) },
  trades_observed: { label: "صفقات مرصودة للثلاثي", target: "≥ 10", show: (v) => String(v ?? 0) },
  replay_match: { label: "الحساب الافتراضي مقابل إعادة التشغيل الأسبوعية", target: "± 30%", show: () => "يُراجع يدويًا" },
};

function CheckRow({ c }: { c: GoCheck }) {
  const meta = CHECKS[c.key];
  const Icon = c.pass === null ? CircleDashed : c.pass ? CheckCircle2 : XCircle;
  return (
    <li className="flex items-center justify-between gap-3 border-b border-border-soft/60 py-2.5 last:border-0">
      <span className="flex min-w-0 items-center gap-2 text-sm text-bone">
        <Icon
          className={cn("h-4 w-4 shrink-0", c.pass === null ? "text-muted-dim" : c.pass ? "text-win-bright" : "text-loss-bright")}
          strokeWidth={1.75}
        />
        {meta.label}
      </span>
      <span className="flex shrink-0 items-baseline gap-3 font-data text-sm">
        <span className={cn(c.pass === false ? "text-loss-bright" : "text-bone")}>{meta.show(c.value)}</span>
        <span className="w-24 text-end text-xs text-muted-dim">{meta.target}</span>
      </span>
    </li>
  );
}

function EquityChart({ data }: { data: VirtualPanel }) {
  const start = Number(data.account?.start_equity ?? 0);
  const points = data.curve.map((p) => ({ t: p.t, equity: Number(p.equity) }));
  if (points.length < 2) {
    return <EmptyState icon={Layers} title="المنحنى يبدأ بعد أول نقطتين" description="تُسجَّل قيمة الحساب كل 5 دقائق." />;
  }
  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={points} margin={{ top: 10, right: 20, left: 10, bottom: 0 }}>
        <defs>
          <linearGradient id="trioFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={GOLD} stopOpacity={0.3} />
            <stop offset="100%" stopColor={GOLD} stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke="#262b33" vertical={false} />
        <XAxis dataKey="t" tickFormatter={(v) => formatDateTime(v).split(",")[0]} stroke={CHART_TEXT} fontSize={11} tickLine={false} minTickGap={40} />
        <YAxis stroke={CHART_TEXT} fontSize={11} tickLine={false} width={70} domain={["auto", "auto"]} />
        {start ? <ReferenceLine y={start} stroke="#3a404b" strokeDasharray="4 4" /> : null}
        <Tooltip
          contentStyle={{ background: "#1b1f26", border: "1px solid #262b33", borderRadius: 6 }}
          labelStyle={{ color: CHART_TEXT }}
          labelFormatter={(v) => formatDateTime(v as string)}
          formatter={(value) => [formatUsd(value as number), "الرصيد"]}
        />
        <Area type="monotone" dataKey="equity" stroke={GOLD} strokeWidth={2} fill="url(#trioFill)" />
      </AreaChart>
    </ResponsiveContainer>
  );
}

function Th({ children, end }: { children: React.ReactNode; end?: boolean }) {
  return <th className={cn("px-3 py-2 font-medium", end && "text-right")}>{children}</th>;
}

export default function TrioPage() {
  const [days, setDays] = useState(28);
  const query = useVirtual(days);
  const d = query.data;
  const acct = d?.account;
  const equity = Number(acct?.equity ?? 0);
  const start = Number(acct?.start_equity ?? 0);
  const change = start ? ((equity / start) - 1) * 100 : null;
  const exposure = equity > 0 ? Number(acct?.notional ?? 0) / equity : null;
  const passed = d?.checks.filter((c) => c.pass).length ?? 0;

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="max-w-2xl">
          <h1 className="text-xl font-semibold text-bone">الثلاثي — مرحلة المراقبة</h1>
          <p className="mt-1 text-sm leading-relaxed text-muted">
            المتداولون الثلاثة على حساب افتراضي واحد بهامش مشترك: كل حركة لهم تُنفَّذ افتراضيًا بسعر Bybit الحي لحظتها مع رسوم
            0.055%، بدون أي أمر حقيقي. بعد 4 أسابيع، لا انتقال للتجريبي إلا إذا نجحت كل الشروط أدناه.
          </p>
        </div>
        <div>
          <Label>الفترة</Label>
          <Select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            <option value={7}>آخر 7 أيام</option>
            <option value={28}>آخر 4 أسابيع</option>
            <option value={90}>آخر 90 يومًا</option>
          </Select>
        </div>
      </div>

      {query.isLoading ? (
        <LoadingState />
      ) : query.isError || !d ? (
        <ErrorState message={(query.error as Error | null)?.message ?? "—"} />
      ) : !acct ? (
        <Card>
          <EmptyState
            icon={Layers}
            title="الحساب الافتراضي لم يبدأ بعد"
            description="يبدأ عندما يعمل البوت باستراتيجية السقف (STRATEGY=cap20_cross) — راجع صفحة الإعدادات."
          />
        </Card>
      ) : (
        <>
          {acct.kill_tripped || acct.liquidations ? (
            <div className="flex flex-col gap-1.5 rounded-md border border-loss/40 bg-loss/10 px-4 py-3 text-sm text-loss-bright">
              {acct.kill_tripped ? (
                <p className="flex items-center gap-2">
                  <AlertTriangle className="h-4 w-4 shrink-0" strokeWidth={1.75} />
                  قاطع الحساب مفعّل: الرصيد نزل تحت 75% من قمته. لا صفقات أو تعزيزات جديدة حتى «استئناف» من صفحة التحكم.
                </p>
              ) : null}
              {acct.liquidations ? (
                <p className="flex items-center gap-2">
                  <AlertTriangle className="h-4 w-4 shrink-0" strokeWidth={1.75} />
                  تعرّض الحساب الافتراضي لتصفية {acct.liquidations} مرة.
                </p>
              ) : null}
            </div>
          ) : null}

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatCard
              label="الرصيد الافتراضي"
              value={formatUsd(acct.equity)}
              tone="gold"
              sub={change === null ? undefined : <span className={pnlClass(change)}>{`${change >= 0 ? "+" : ""}${formatNumber(change, 2)}% من ${formatUsd(acct.start_equity)}`}</span>}
            />
            <StatCard
              label="غير المحقق الآن"
              value={formatMoney(acct.unrealized)}
              tone={pnlTone(acct.unrealized)}
              sub={`${acct.positions.length} مركز صافٍ · رسوم مدفوعة ${formatUsd(acct.fees_paid)}`}
            />
            <StatCard
              label="الحجم ÷ الرصيد"
              value={exposure === null ? "—" : `${formatNumber(exposure, 2)}x`}
              tone={exposure !== null && exposure > 4 ? "loss" : "default"}
              sub={`الحد 6x · ${formatUsd(acct.notional)}`}
            />
            <StatCard
              label="البعد عن التصفية"
              value={acct.liquidation_distance ? `${formatNumber(acct.liquidation_distance, 1)}x` : "لا مراكز"}
              tone={acct.liquidation_distance && Number(acct.liquidation_distance) < 3 ? "loss" : "default"}
              sub={acct.peak ? `القمة ${formatUsd(acct.peak)}` : "الرصيد ÷ هامش الصيانة — 1x = تصفية"}
            />
            <StatCard
              label="زمن الالتقاط p50 / p95"
              value={`${formatMs(d.stats.latency_p50_ms)} / ${formatMs(d.stats.latency_p95_ms)}`}
              sub={`${d.stats.events} حركة في الفترة`}
            />
            <StatCard
              label="وسيط الفرق عن سعره"
              value={d.stats.slippage_median_bps === null ? "—" : `${formatNumber(d.stats.slippage_median_bps / 100, 3)}%`}
              tone={d.stats.slippage_median_bps !== null && d.stats.slippage_median_bps > 15 ? "loss" : "default"}
              sub={`سعر Bybit (شراء/بيع) مقابل سعر المتداول · ${d.stats.slippage_samples} عينة`}
            />
            <StatCard
              label="صفقات الثلاثي"
              value={d.lanes.reduce((n, l) => n + l.trades, 0)}
              sub={`${d.open.length} مفتوحة الآن`}
            />
            <StatCard label="شروط الانتقال" value={`${passed} / ${d.checks.length}`} tone={passed === d.checks.length ? "win" : "default"} sub="انظر القائمة أدناه" />
          </div>

          <Card>
            <CardHeader title="منحنى الحساب الافتراضي" subtitle="قيمة الحساب كل 5 دقائق، والخط المتقطع هو رصيد البداية" />
            <div className="h-72 p-4 pl-0">
              <EquityChart data={d} />
            </div>
          </Card>

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
            <Card className="lg:col-span-3">
              <CardHeader title="المتداولون" subtitle="كل متداول يُحسب حجمه على رصيد الحساب كاملًا" />
              <div className="overflow-x-auto">
                <table className="w-full min-w-[640px] text-left text-sm">
                  <thead>
                    <tr className="border-b border-border-soft text-[11px] text-muted-dim">
                      <Th>المتداول</Th>
                      <Th>الوضع</Th>
                      <Th end>صفقات</Th>
                      <Th end>مفتوحة</Th>
                      <Th end>نسبة الربح</Th>
                      <Th end>الصافي</Th>
                      <Th end>تعزيزات</Th>
                      <Th end>بلغت السقف</Th>
                      <Th end>رصيده</Th>
                    </tr>
                  </thead>
                  <tbody className="font-data whitespace-nowrap">
                    {d.lanes.map((l) => (
                      <tr key={l.label} className="border-b border-border-soft/60">
                        <td className="px-3 py-2 font-sans">
                          <Link href={`/trades?leader=${l.label}`} className="hover:underline"><LeaderBadge leader={l.label} /></Link>
                        </td>
                        <td className="px-3 py-2 font-sans">{l.paused ? <span className="text-xs text-loss-bright">موقوف</span> : <ModeBadge mode={l.mode} />}</td>
                        <td className="px-3 py-2 text-right text-bone">{l.trades}</td>
                        <td className="px-3 py-2 text-right text-muted">{l.open}</td>
                        <td className="px-3 py-2 text-right text-muted">{l.win_rate === null ? "—" : formatPercent(l.win_rate * 100, 0)}</td>
                        <td className={cn("px-3 py-2 text-right", pnlClass(l.realized_pnl))}>{formatMoney(l.realized_pnl)}</td>
                        <td className="px-3 py-2 text-right text-muted">{l.adds}</td>
                        <td className="px-3 py-2 text-right text-muted">{l.capped}</td>
                        <td className="px-3 py-2 text-right text-muted">{formatUsd(l.leader_equity)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="border-t border-border-soft px-4 py-2.5 text-xs leading-relaxed text-muted-dim">
                متداول هادئ لأسابيع أمر طبيعي (8fc7 توقف 130 يومًا في سنة البحث وبقي رابحًا) — ليس عطلًا.
              </p>
            </Card>

            <Card className="lg:col-span-2">
              <CardHeader title="شروط الانتقال إلى التجريبي" subtitle="botup.md §V5.6 — كلها يجب أن تنجح" />
              <ul className="px-4 py-1">
                {d.checks.map((c) => <CheckRow key={c.key} c={c} />)}
              </ul>
            </Card>
          </div>

          <Card>
            <CardHeader title="الصفقات المفتوحة" subtitle="حصة كل متداول — الحساب يحمل صافي الحصص لكل عملة" />
            {!d.open.length ? (
              <EmptyState icon={Layers} title="لا صفقات مفتوحة الآن" />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[900px] text-left text-sm">
                  <thead>
                    <tr className="border-b border-border-soft text-[11px] text-muted-dim">
                      <Th>المتداول</Th>
                      <Th>الرمز</Th>
                      <Th>الاتجاه</Th>
                      <Th end>الكمية</Th>
                      <Th end>دخولنا</Th>
                      <Th end>R</Th>
                      <Th end>السقف المستخدم</Th>
                      <Th end>تعزيزات</Th>
                      <Th end>مركزه</Th>
                      <Th>منذ</Th>
                    </tr>
                  </thead>
                  <tbody className="font-data whitespace-nowrap">
                    {d.open.map((c) => (
                      <tr key={c.id} className="border-b border-border-soft/60">
                        <td className="px-3 py-2 font-sans"><LeaderBadge leader={c.leader} /></td>
                        <td className="px-3 py-2 text-bone">
                          <Link href={`/trades?open=${c.id}`} className="hover:underline">{c.symbol}</Link>
                        </td>
                        <td className="px-3 py-2"><SideBadge side={c.side} /></td>
                        <td className="px-3 py-2 text-right text-bone">{formatNumber(c.qty_open, 6)}</td>
                        <td className="px-3 py-2 text-right text-muted">{formatPrice(c.entry_price)}</td>
                        <td className="px-3 py-2 text-right text-muted">{formatRatio(c.ratio_r)}</td>
                        <td className={cn("px-3 py-2 text-right", c.capped ? "text-warn" : "text-muted")}>
                          {c.cap_used_pct === null ? "—" : `${formatNumber(c.cap_used_pct, 0)}%`}
                        </td>
                        <td className="px-3 py-2 text-right text-muted">{c.adds_done}</td>
                        <td className="px-3 py-2 text-right text-muted">{formatNumber(c.leader_pos, 6)}</td>
                        <td className="px-3 py-2 text-muted">{formatRelativeTime(c.opened_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          {acct.positions.length ? (
            <Card>
              <CardHeader title="مراكز الحساب الصافية" subtitle="ما يحمله الحساب فعلًا لكل عملة بعد جمع حصص المتداولين" />
              <div className="overflow-x-auto">
                <table className="w-full min-w-[640px] text-left text-sm">
                  <thead>
                    <tr className="border-b border-border-soft text-[11px] text-muted-dim">
                      <Th>الرمز</Th>
                      <Th>الاتجاه</Th>
                      <Th end>الكمية</Th>
                      <Th end>متوسط الدخول</Th>
                      <Th end>سعر العلامة</Th>
                      <Th end>غير محقق</Th>
                      <Th end>هامش الصيانة</Th>
                    </tr>
                  </thead>
                  <tbody className="font-data whitespace-nowrap">
                    {acct.positions.map((p) => (
                      <tr key={p.symbol} className="border-b border-border-soft/60">
                        <td className="px-3 py-2 text-bone">{p.symbol}</td>
                        <td className="px-3 py-2"><SideBadge side={p.side > 0 ? "LONG" : "SHORT"} /></td>
                        <td className="px-3 py-2 text-right text-bone">{formatNumber(p.qty, 6)}</td>
                        <td className="px-3 py-2 text-right text-muted">{formatPrice(p.entry)}</td>
                        <td className="px-3 py-2 text-right text-muted">{formatPrice(p.mark)}</td>
                        <td className={cn("px-3 py-2 text-right", pnlClass(p.upnl))}>{formatMoney(p.upnl)}</td>
                        <td className="px-3 py-2 text-right text-muted">{formatNumber(Number(p.mmr) * 100, 2)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          ) : null}

          <Card>
            <CardHeader title="آخر حركات الثلاثي" subtitle="سعر المتداول مقابل السعر الذي كنا سنحصل عليه في Bybit لحظة التقاطها" />
            {!d.recent.length ? (
              <EmptyState icon={Layers} title="لا حركات بعد" description="المتداولون الثلاثة يفتحون نحو 5 صفقات في الأسبوع معًا." />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[960px] text-left text-sm">
                  <thead>
                    <tr className="border-b border-border-soft text-[11px] text-muted-dim">
                      <Th>الوقت</Th>
                      <Th>المتداول</Th>
                      <Th>العملة</Th>
                      <Th>الحركة</Th>
                      <Th end>سعره</Th>
                      <Th end>سعرنا</Th>
                      <Th end>الفرق</Th>
                      <Th end>الالتقاط</Th>
                      <Th>القرار</Th>
                    </tr>
                  </thead>
                  <tbody className="font-data whitespace-nowrap">
                    {d.recent.map((e) => (
                      <tr key={e.id} className="border-b border-border-soft/60">
                        <td className="px-3 py-2 text-muted">{formatDateTime(e.detected_at)}</td>
                        <td className="px-3 py-2 font-sans"><LeaderBadge leader={e.leader} /></td>
                        <td className="px-3 py-2 text-bone">{e.coin}</td>
                        <td className="px-3 py-2"><EventTypeBadge type={e.type} /></td>
                        <td className="px-3 py-2 text-right text-bone">{formatPrice(e.leader_px)}</td>
                        <td className="px-3 py-2 text-right text-bone">{formatPrice(e.our_px)}</td>
                        <td className={cn("px-3 py-2 text-right", (e.slippage_bps ?? 0) > 15 ? "text-warn" : "text-muted")}>
                          {e.slippage_bps === null ? "—" : `${formatNumber(e.slippage_bps, 1)} bps`}
                        </td>
                        <td className={cn("px-3 py-2 text-right", e.lag_ms > 10_000 ? "text-warn" : "text-muted")}>{formatMs(e.lag_ms)}</td>
                        <td className="px-3 py-2"><DecisionBadge decision={e.decision} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
