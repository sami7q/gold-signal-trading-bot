"use client";

import { useState } from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { LineChart as LineChartIcon } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/card";
import { StatCard } from "@/components/ui/stat-card";
import { Badge } from "@/components/ui/badge";
import { Label, Select } from "@/components/ui/input";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/empty-state";
import { LeaderSelect } from "@/components/trade/leader-select";
import { useAnalytics } from "@/hooks/use-loco";
import { arCopyStatus, arRejection, formatDateTime, formatMoney, formatMs, formatNumber, formatPercent, pnlTone } from "@/lib/format";
import type { Metrics } from "@/types/api";
import { urlParam } from "@/lib/url";

const CHART_TEXT = "#8b8f98";
const GOLD = "#c9a227";
const WIN = "#4f9d69";
const LOSS = "#c1554a";

type Check = { label: string; value: string; target: string; ok: boolean | null };

// botup.md §11 -- the go / no-go targets for the 30-day demo.
function goNoGo(m: Metrics): Check[] {
  return [
    { label: "زمن الالتقاط p50 / p95", value: `${formatMs(m.detect_p50_ms)} / ${formatMs(m.detect_p95_ms)}`, target: "< 1 ث / < 3 ث",
      ok: m.detect_p50_ms === null ? null : m.detect_p50_ms < 1000 && (m.detect_p95_ms ?? 0) < 3000 },
    { label: "زمن التنفيذ p50", value: formatMs(m.order_p50_ms), target: "< 1 ث", ok: m.order_p50_ms === null ? null : m.order_p50_ms < 1000 },
    { label: "انزلاق الدخول (الوسيط)", value: m.slippage_median_bps === null ? "—" : `${formatNumber(m.slippage_median_bps, 1)} bps`, target: "< 10 bps",
      ok: m.slippage_median_bps === null ? null : m.slippage_median_bps < 10 },
    { label: "نسبة الالتقاط", value: m.capture_rate === null ? "—" : formatPercent(m.capture_rate * 100), target: "≥ 90%",
      ok: m.capture_rate === null ? null : m.capture_rate >= 0.9 },
    { label: "إغلاقات فائتة", value: String(m.missed_exits), target: "0", ok: m.missed_exits === 0 },
  ];
}

export default function AnalyticsPage() {
  const [days, setDays] = useState(30);
  const [leader, setLeader] = useState(() => urlParam("leader") ?? "");
  const query = useAnalytics(days, leader || undefined);
  const data = query.data;
  const m = data?.metrics;

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-bone">التحليلات</h1>
          <p className="mt-1 text-sm text-muted">الأداء من الصفقات المغلقة فعليًا، ومقاييس الانتقال من التجريبي إلى الحقيقي.</p>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <LeaderSelect value={leader} onChange={setLeader} />
          <div>
          <Label>الفترة</Label>
          <Select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            <option value={7}>آخر 7 أيام</option>
            <option value={30}>آخر 30 يومًا</option>
            <option value={90}>آخر 90 يومًا</option>
            <option value={3650}>كل الفترة</option>
          </Select>
          </div>
        </div>
      </div>

      {query.isLoading ? (
        <LoadingState />
      ) : query.isError || !data || !m ? (
        <ErrorState message={(query.error as Error | null)?.message ?? "—"} />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatCard label="صافي الربح" value={formatMoney(m.realized_pnl)} tone={pnlTone(m.realized_pnl)} />
            <StatCard label="حركات فتح المتداول" value={m.leader_opens} sub={`نُسخ ${m.copied_opens} · غير مدرجة ${m.not_listed}`} />
            <StatCard label="هدف ربح / مع المتداول" value={`${m.take_profits} / ${m.leader_exits}`} tone="win" />
            <StatCard label="تصفيات" value={m.liquidations} tone={m.liquidations ? "loss" : "default"} />
          </div>

          <Card>
            <CardHeader title="منحنى الربح" subtitle="الربح التراكمي عبر الصفقات المغلقة" />
            <div className="h-72 p-4 pl-0">
              {!data.curve.length ? (
                <EmptyState icon={LineChartIcon} title="لا توجد صفقات مغلقة في هذه الفترة" />
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={data.curve.map((p) => ({ ...p, cumulative: Number(p.cumulative) }))} margin={{ top: 10, right: 20, left: 10, bottom: 0 }}>
                    <defs>
                      <linearGradient id="profitFill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor={GOLD} stopOpacity={0.35} />
                        <stop offset="100%" stopColor={GOLD} stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid stroke="#262b33" vertical={false} />
                    <XAxis dataKey="closed_at" tickFormatter={(v) => formatDateTime(v).split(",")[0]} stroke={CHART_TEXT} fontSize={11} tickLine={false} />
                    <YAxis stroke={CHART_TEXT} fontSize={11} tickLine={false} width={70} />
                    <Tooltip
                      contentStyle={{ background: "#1b1f26", border: "1px solid #262b33", borderRadius: 6 }}
                      labelStyle={{ color: CHART_TEXT }}
                      labelFormatter={(v) => formatDateTime(v as string)}
                      formatter={(value) => [formatMoney(value as number), "تراكمي"]}
                    />
                    <Area type="monotone" dataKey="cumulative" stroke={GOLD} strokeWidth={2} fill="url(#profitFill)" />
                  </AreaChart>
                </ResponsiveContainer>
              )}
            </div>
          </Card>

          <Card>
            <CardHeader title="الرابحة مقابل الخاسرة يوميًا" subtitle="حسب يوم الإغلاق (UTC)" />
            <div className="h-64 p-4 pl-0">
              {!data.daily.length ? (
                <EmptyState icon={LineChartIcon} title="لا توجد صفقات مغلقة في هذه الفترة" />
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={data.daily} margin={{ top: 10, right: 20, left: 10, bottom: 0 }}>
                    <CartesianGrid stroke="#262b33" vertical={false} />
                    <XAxis dataKey="day" stroke={CHART_TEXT} fontSize={11} tickLine={false} />
                    <YAxis stroke={CHART_TEXT} fontSize={11} tickLine={false} allowDecimals={false} width={30} />
                    <Tooltip contentStyle={{ background: "#1b1f26", border: "1px solid #262b33", borderRadius: 6 }} labelStyle={{ color: CHART_TEXT }} />
                    <Legend wrapperStyle={{ fontSize: 12, color: CHART_TEXT }} />
                    <Bar dataKey="wins" name="رابحة" fill={WIN} radius={[2, 2, 0, 0]} />
                    <Bar dataKey="losses" name="خاسرة" fill={LOSS} radius={[2, 2, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </div>
          </Card>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader title="مقاييس الجاهزية" subtitle="أهداف الانتقال من التجريبي إلى الحقيقي (botup.md §11)" />
              <table className="w-full text-left text-sm">
                <tbody>
                  {goNoGo(m).map((c) => (
                    <tr key={c.label} className="border-b border-border-soft/60 last:border-0">
                      <td className="px-4 py-2 text-bone">{c.label}</td>
                      <td className="font-data px-4 py-2 text-right text-bone">{c.value}</td>
                      <td className="font-data px-4 py-2 text-right text-muted-dim">{c.target}</td>
                      <td className="px-4 py-2 text-right">
                        {c.ok === null ? <Badge>لا بيانات</Badge> : c.ok ? <Badge tone="win">ناجح</Badge> : <Badge tone="loss">لم يتحقق</Badge>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>

            <Card>
              <CardHeader title="أسباب الرفض وطرق الإغلاق" subtitle="خلال الفترة المختارة" />
              <div className="flex flex-col gap-4 p-4 text-sm">
                <div className="flex flex-wrap gap-2">
                  {Object.entries(data.by_close).filter(([, n]) => n > 0).map(([status, n]) => (
                    <Badge key={status} tone="neutral">{arCopyStatus(status)}: {n}</Badge>
                  ))}
                  {!Object.values(data.by_close).some((n) => n > 0) ? <span className="text-xs text-muted-dim">لا إغلاقات بعد</span> : null}
                </div>
                <ul className="divide-y divide-border-soft/60">
                  {Object.entries(m.rejections).sort((a, b) => b[1] - a[1]).map(([reason, n]) => (
                    <li key={reason} className="flex justify-between py-1.5">
                      <span className="text-muted">{arRejection(reason)}</span>
                      <span className="font-data text-bone">{n}</span>
                    </li>
                  ))}
                  {!Object.keys(m.rejections).length ? <li className="py-1.5 text-xs text-muted-dim">لا رفض في هذه الفترة</li> : null}
                </ul>
              </div>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
