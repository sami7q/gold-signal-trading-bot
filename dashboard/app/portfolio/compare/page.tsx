"use client";

// botup.md §V10: the simulator sections side by side -- the trio and the two mixes, on the same fills.

import { useState } from "react";
import Link from "next/link";
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { FlaskConical, Trophy } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/card";
import { StatCard } from "@/components/ui/stat-card";
import { Label, Select } from "@/components/ui/input";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/empty-state";
import { usePortfolioCompare } from "@/hooks/use-loco";
import { isNotFound } from "@/lib/api";
import { ServerOutdated } from "@/components/ui/server-outdated";
import { cn, formatDateTime, formatMoney, formatNumber, formatUsd, pnlClass } from "@/lib/format";
import type { PfCompareGroup, PortfolioCompare } from "@/types/api";

const CHART_TEXT = "#8b8f98";
const COLORS = ["#c9a227", "#5b8def", "#4cc38a", "#c26bd6"];
const DAY_MS = 86_400_000;
type Scenario = "real" | "base";
const SCENARIO_AR: Record<Scenario, string> = { real: "واقعي", base: "أساسي" };

const HREF: Record<string, string> = { trio: "/portfolio" };

function name(g: PfCompareGroup): string {
  // \u2066...\u2069 isolates the label: "2ac4" starts with a digit and would jump around in RTL text
  return g.name === "trio" ? "الثلاثي الحالي" : `الرباعي مع \u2066${g.labels[g.labels.length - 1]}\u2069`;
}

function usd0(v: number): string {
  return `$${Math.round(v).toLocaleString("en-US")}`;
}

function signedPct(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return "—";
  return `${v >= 0 ? "+" : ""}${formatNumber(v, digits)}%`;
}

function Th({ children, end }: { children?: React.ReactNode; end?: boolean }) {
  return <th className={cn("px-3 py-2 font-medium", end && "text-right")}>{children}</th>;
}

function GroupName({ g, i }: { g: PfCompareGroup; i: number }) {
  return (
    <Link href={HREF[g.name] ?? `/portfolio/${g.name}`} className="flex items-center gap-2 hover:underline">
      <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: COLORS[i % COLORS.length] }} />
      <span dir="rtl" className="text-bone">{name(g)}</span>
      <bdi dir="ltr" className="font-data text-[11px] text-muted">{g.labels.join("+")}</bdi>
    </Link>
  );
}

function CommonChart({ d, scenario }: { d: PortfolioCompare; scenario: Scenario }) {
  const groups = d.groups ?? [];
  const common = d.common_start_ms ?? 0;
  const byT = new Map<number, Record<string, number>>();
  groups.forEach((g, i) => {
    const pts = g.curves[scenario].filter((p) => p.ms >= common);
    const first = pts[0]?.equity;
    if (!first) return;
    for (const p of pts) {
      const row = byT.get(p.ms) ?? { ms: p.ms };
      row[`g${i}`] = (p.equity / first - 1) * 100;
      byT.set(p.ms, row);
    }
  });
  const data = [...byT.values()].sort((a, b) => a.ms - b.ms);
  if (data.length < 2) {
    return <EmptyState icon={FlaskConical} title="المنحنى يبدأ بعد أول نقطتين من البداية المشتركة" description="تُسجَّل قيمة كل حساب كل 5 دقائق." />;
  }
  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={data} margin={{ top: 10, right: 20, left: 10, bottom: 0 }}>
        <CartesianGrid stroke="#262b33" vertical={false} />
        <XAxis
          dataKey="ms"
          type="number"
          domain={["dataMin", "dataMax"]}
          tickFormatter={(v) => formatDateTime(new Date(v).toISOString()).split(",")[0]}
          stroke={CHART_TEXT}
          fontSize={11}
          tickLine={false}
          minTickGap={40}
        />
        <YAxis stroke={CHART_TEXT} fontSize={11} tickLine={false} width={60} tickFormatter={(v) => `${formatNumber(v, Math.abs(v) < 1 ? 2 : 1)}%`} />
        <ReferenceLine y={0} stroke="#3a404b" strokeDasharray="4 4" />
        <Tooltip
          contentStyle={{ background: "#1b1f26", border: "1px solid #262b33", borderRadius: 6 }}
          labelStyle={{ color: CHART_TEXT }}
          labelFormatter={(v) => formatDateTime(new Date(v as number).toISOString())}
          formatter={(value, n) => [signedPct(value as number), n]}
        />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        {groups.map((g, i) => (
          <Line key={g.name} type="monotone" dataKey={`g${i}`} name={name(g)} stroke={COLORS[i % COLORS.length]} strokeWidth={i ? 1.75 : 2} dot={false} connectNulls isAnimationActive={false} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

function Body({ d, scenario }: { d: PortfolioCompare; scenario: Scenario }) {
  const groups = d.groups ?? [];
  const cap = String(d.capital);
  const capitals = d.capitals ?? [];
  const common = d.common_start_ms ?? 0;
  const commonDays = Math.max(0, ((d.updated_ms ?? common) - common) / DAY_MS);
  const ranked = [...groups].sort((a, b) => (b.since_common[scenario] ?? -1e9) - (a.since_common[scenario] ?? -1e9));
  // a leader only when it is ahead by a real margin (0.1 point) -- at the start every section sits at ~0 %
  const gap = (ranked[0]?.since_common[scenario] ?? 0) - (ranked[1]?.since_common[scenario] ?? 0);
  const leader = ranked.length > 1 && gap >= 0.1 ? ranked[0] : undefined;
  const wallets = [...new Set(groups.flatMap((g) => g.labels))];
  return (
    <>
      <div className={cn("grid grid-cols-1 gap-3", groups.length >= 3 ? "sm:grid-cols-3" : "sm:grid-cols-2")}>
        {groups.map((g) => {
          const l = g.capitals[cap]?.[scenario];
          return (
            <StatCard
              key={g.name}
              label={`${name(g)} — ${SCENARIO_AR[scenario]}`}
              value={formatUsd(l?.equity ?? null)}
              tone={g.name === leader?.name && (leader.since_common[scenario] ?? 0) !== 0 ? "gold" : "default"}
              sub={
                <span>
                  <span className={pnlClass(g.since_common[scenario] ?? 0)}>{signedPct(g.since_common[scenario])}</span>
                  {` من البداية المشتركة · تراجع أقصى ${formatNumber(l?.max_drawdown_pct ?? 0, 1)}%`}
                </span>
              }
            />
          );
        })}
      </div>

      <Card>
        <CardHeader
          title={`المقارنة العادلة — من البداية المشتركة (${formatNumber(commonDays, 1)} يوم)`}
          subtitle={`كل الأقسام على نفس الحركات منذ ${formatDateTime(new Date(common).toISOString())} (بدء آخر قسم) — ${formatUsd(d.capital)} · ${SCENARIO_AR[scenario]} · العائد من تلك اللحظة`}
          action={leader && (leader.since_common[scenario] ?? null) !== null ? (
            <span className="flex items-center gap-1.5 text-xs text-gold-bright">
              <Trophy className="h-3.5 w-3.5" strokeWidth={1.75} /> المتقدّم الآن: {name(leader)}
            </span>
          ) : null}
        />
        <div className="h-80 p-4 pl-0" dir="ltr">
          <CommonChart d={d} scenario={scenario} />
        </div>
      </Card>

      <Card>
        <CardHeader title="كل قسم منذ بدايته" subtitle={`${formatUsd(d.capital)} — الثلاثي بدأ قبل الرباعيين، لذلك أرقامه هنا على فترة أطول`} />
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1100px] text-left text-sm">
            <thead>
              <tr className="border-b border-border-soft text-[11px] text-muted-dim">
                <Th>القسم</Th>
                <Th>بدأ</Th>
                <Th end>أيام</Th>
                <Th end>واقعي</Th>
                <Th end>%</Th>
                <Th end>أساسي</Th>
                <Th end>%</Th>
                <Th end>أقصى تراجع</Th>
                <Th end>صفقات مغلقة</Th>
                <Th end>نسبة الربح</Th>
                <Th end>مفتوحة</Th>
                <Th end>الرسوم</Th>
                <Th end>تصفيات</Th>
              </tr>
            </thead>
            <tbody className="font-data whitespace-nowrap">
              {groups.map((g, i) => {
                const r = g.capitals[cap]?.real;
                const b = g.capitals[cap]?.base;
                const l = scenario === "real" ? r : b;
                return (
                  <tr key={g.name} className="border-b border-border-soft/60">
                    <td className="px-3 py-2 font-sans"><GroupName g={g} i={i} /></td>
                    <td className="px-3 py-2 text-muted">{formatDateTime(new Date(g.started_ms).toISOString())}</td>
                    <td className="px-3 py-2 text-right text-muted">{formatNumber(g.days, 1)}</td>
                    <td className="px-3 py-2 text-right text-bone">{formatUsd(r?.equity ?? null)}</td>
                    <td className={cn("px-3 py-2 text-right", pnlClass(r?.return_pct ?? 0))}>{signedPct(r?.return_pct)}</td>
                    <td className="px-3 py-2 text-right text-muted">{formatUsd(b?.equity ?? null)}</td>
                    <td className={cn("px-3 py-2 text-right", pnlClass(b?.return_pct ?? 0))}>{signedPct(b?.return_pct)}</td>
                    <td className={cn("px-3 py-2 text-right", (l?.max_drawdown_pct ?? 0) >= 20 ? "text-loss-bright" : "text-muted")}>
                      {`${formatNumber(l?.max_drawdown_pct ?? 0, 1)}%`}
                    </td>
                    <td className="px-3 py-2 text-right text-bone">{l?.trades ?? 0}</td>
                    <td className="px-3 py-2 text-right text-muted">{l?.win_rate === null || l?.win_rate === undefined ? "—" : `${formatNumber(l.win_rate * 100, 0)}%`}</td>
                    <td className="px-3 py-2 text-right text-muted">{l?.open_trades ?? 0}</td>
                    <td className="px-3 py-2 text-right text-loss-bright">{formatMoney(-(l?.fees_paid ?? 0))}</td>
                    <td className={cn("px-3 py-2 text-right", l?.liquidations ? "text-loss-bright" : "text-muted")}>{l?.liquidations ?? 0}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-6">
        <Card>
          <CardHeader title={`الأرصدة الثلاثة — ${SCENARIO_AR[scenario]}`} subtitle="نفس الحركات على $10,000 و$50,000 و$100,000 في كل قسم" />
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px] text-left text-sm">
              <thead>
                <tr className="border-b border-border-soft text-[11px] text-muted-dim">
                  <Th>رأس المال</Th>
                  {groups.map((g) => <Th key={g.name} end>{name(g)}</Th>)}
                </tr>
              </thead>
              <tbody className="font-data whitespace-nowrap">
                {capitals.map((c) => (
                  <tr key={c} className={cn("border-b border-border-soft/60", c === d.capital && "bg-gold/5")}>
                    <td className="px-3 py-2 text-bone">{formatUsd(c)}</td>
                    {groups.map((g) => {
                      const l = g.capitals[String(c)]?.[scenario];
                      return (
                        <td key={g.name} className="px-3 py-2 text-right">
                          <span className="text-bone">{formatUsd(l?.equity ?? null)}</span>{" "}
                          <span className={cn("text-xs", pnlClass(l?.return_pct ?? 0))}>{signedPct(l?.return_pct)}</span>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card>
          <CardHeader title={`ربح كل متداول — ${SCENARIO_AR[scenario]} · ${formatUsd(d.capital)}`} subtitle="الصافي من الصفقات المغلقة في كل قسم منذ بدايته (بعد الرسوم)" />
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px] text-left text-sm">
              <thead>
                <tr className="border-b border-border-soft text-[11px] text-muted-dim">
                  <Th>المتداول</Th>
                  {groups.map((g) => <Th key={g.name} end>{name(g)}</Th>)}
                </tr>
              </thead>
              <tbody className="font-data whitespace-nowrap">
                {wallets.map((w) => (
                  <tr key={w} className="border-b border-border-soft/60">
                    <td className="px-3 py-2 text-bone"><bdi>0x{w}</bdi></td>
                    {groups.map((g) => {
                      if (!g.labels.includes(w)) return <td key={g.name} className="px-3 py-2 text-right text-muted-dim">—</td>;
                      const s = g.capitals[cap]?.[scenario]?.per_wallet?.[w];
                      return (
                        <td key={g.name} className={cn("px-3 py-2 text-right", pnlClass(s?.pnl ?? 0))}>
                          {formatMoney(s?.pnl ?? 0)} <span className="text-xs text-muted">({s?.trades ?? 0})</span>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      <Card>
        <CardHeader title="نتيجة البحث لكل قسم (للمرجع)" subtitle="سنة كاملة سابقة، $10,000، واقعي بسقف $50,000 للصفقة — نفس الطريقة للأقسام الثلاثة (قسم البحث 2026-10-14)" />
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead>
              <tr className="border-b border-border-soft text-[11px] text-muted-dim">
                <Th>القسم</Th>
                <Th end>واقعي سنة</Th>
                <Th end>متحفظ (نصف الأفضلية)</Th>
                <Th end>ما يعادله لهذه المدة (واقعي)</Th>
              </tr>
            </thead>
            <tbody className="font-data whitespace-nowrap">
              {groups.map((g, i) => (
                <tr key={g.name} className="border-b border-border-soft/60">
                  <td className="px-3 py-2 font-sans"><GroupName g={g} i={i} /></td>
                  <td className="px-3 py-2 text-right text-bone">{g.research_year ? `${usd0(10000 * g.research_year)} (${signedPct((g.research_year - 1) * 100, 0)})` : "—"}</td>
                  <td className="px-3 py-2 text-right text-muted">{g.research_half ? `${usd0(10000 * g.research_half)} (${signedPct((g.research_half - 1) * 100, 0)})` : "—"}</td>
                  <td className="px-3 py-2 text-right text-muted">
                    {g.research_year ? signedPct((Math.pow(g.research_year, Math.max(g.days, 0) / 365) - 1) * 100, 1) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="border-t border-border-soft px-4 py-2.5 text-xs leading-relaxed text-muted-dim">
          أسابيع قليلة لا تكفي للحكم: الفرق بين الأقسام يأتي من صفقات المحفظة الرابعة فقط (2ac4 أو 2396)، والباقي نفس صفقات الثلاثي بنفس الحجم.
          محاكاة فقط — ليست نصيحة مالية.
        </p>
      </Card>
    </>
  );
}

export default function PortfolioComparePage() {
  const [capital, setCapital] = useState<number | undefined>(undefined);
  const [scenario, setScenario] = useState<Scenario>("real");
  const [leverage, setLeverage] = useState<number | undefined>(undefined);
  const q = usePortfolioCompare(capital, leverage);
  const d = q.data;
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="max-w-2xl">
          <h1 className="text-xl font-semibold text-bone">مقارنة المحاكيات</h1>
          <p className="mt-1 text-sm leading-relaxed text-muted">
            الثلاثي الحالي مقابل الرباعي <bdi dir="ltr">8bae + 8fc7 + 69b0 + 2ac4</bdi> والرباعي <bdi dir="ltr">8bae + 8fc7 + 69b0 + 2396</bdi> —
            نفس الحركات الحية، نفس القواعد (3x، سقف 20%، رسوم كل أمر)، كل قسم في حسابه الوهمي.
          </p>
        </div>
        <div className="flex gap-3">
          <div>
            <Label>رأس المال</Label>
            <Select value={capital ?? d?.capital ?? ""} onChange={(e) => setCapital(Number(e.target.value))}>
              {(d?.capitals ?? [10000, 50000, 100000]).map((c) => (
                <option key={c} value={c}>{formatUsd(c)}</option>
              ))}
            </Select>
          </div>
          {d?.leverages && d.leverages.length > 1 ? (
            <div>
              <Label>الرافعة</Label>
              <Select value={leverage ?? d.leverage ?? d.leverages[0]} onChange={(e) => setLeverage(Number(e.target.value))}>
                {d.leverages.map((x) => (
                  <option key={x} value={x}>{`${x}x`}</option>
                ))}
              </Select>
            </div>
          ) : null}
          <div>
            <Label>السيناريو</Label>
            <Select value={scenario} onChange={(e) => setScenario(e.target.value as Scenario)}>
              <option value="real">واقعي</option>
              <option value="base">أساسي</option>
            </Select>
          </div>
        </div>
      </div>
      {q.isLoading ? (
        <LoadingState />
      ) : isNotFound(q.error) ? (
        <ServerOutdated what="صفحة المقارنة" />
      ) : q.isError || !d ? (
        <ErrorState message={(q.error as Error | null)?.message ?? "—"} />
      ) : !d.enabled || !d.groups?.length ? (
        <Card>
          <EmptyState icon={FlaskConical} title="المحاكي لم يبدأ بعد" description="حدّث البوت على السيرفر (botup.md §V10) وأعد تشغيله وتشغيل واجهة اللوحة." />
        </Card>
      ) : d.groups.length < 2 ? (
        <Card>
          <EmptyState icon={FlaskConical} title="قسم واحد فقط على السيرفر" description="البوت على السيرفر بإصدار قبل §V10 — حدّثه ليبدأ الرباعيان." />
        </Card>
      ) : (
        <Body d={d} scenario={scenario} />
      )}
    </div>
  );
}
