"use client";

import { useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AlertTriangle, CheckCircle2, CircleDashed, FlaskConical, XCircle } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/card";
import { StatCard } from "@/components/ui/stat-card";
import { Badge } from "@/components/ui/badge";
import { Label, Select } from "@/components/ui/input";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/empty-state";
import { usePortfolio } from "@/hooks/use-loco";
import {
  cn,
  formatDateTime,
  formatDuration,
  formatMoney,
  formatMs,
  formatNumber,
  formatPrice,
  formatRelativeTime,
  formatUsd,
  pnlClass,
  pnlTone,
} from "@/lib/format";
import type { PfCheck, PfLedger, PortfolioPanel } from "@/types/api";

const CHART_TEXT = "#8b8f98";
const COLORS = { real: "#c9a227", base: "#5b8def", expected: "#6b7280", walk: "#3a404b" };
const DAY_MS = 86_400_000;

type Scenario = "real" | "base";
const SCENARIO_AR: Record<Scenario, string> = { real: "واقعي", base: "أساسي" };

// What one wallet fill did on a ledger (loco_copy/pf_ledger.py Outcome.action).
const ACTION_AR: Record<string, { label: string; tone: "gold" | "win" | "loss" | "warn" | "info" | "neutral" }> = {
  OPEN: { label: "فتح", tone: "gold" },
  ADD: { label: "تعزيز", tone: "win" },
  REDUCE: { label: "تقليص", tone: "info" },
  CLOSE: { label: "إغلاق", tone: "info" },
  FLIP: { label: "انعكاس", tone: "info" },
  PENDING: { label: "مؤجّل (< $10)", tone: "neutral" },
  CAPPED: { label: "بلغ السقف", tone: "warn" },
  EXPOSURE: { label: "حد 6x", tone: "warn" },
  WAIT: { label: "مركز قديم — ينتظر", tone: "neutral" },
  IGNORED: { label: "لا شيء", tone: "neutral" },
  SKIP_PAUSED: { label: "موقوف (تراجع 25%)", tone: "loss" },
  SKIP_STALE: { label: "متأخر — لم يُنسخ", tone: "loss" },
  SKIP_NO_EQUITY: { label: "رصيده غير معروف", tone: "loss" },
};

const REASON_AR: Record<string, string> = {
  CLOSE: "أغلق مع المحفظة",
  FLIP: "انعكاس",
  RECONCILED: "إغلاق تصحيحي",
  LIQUIDATED: "تصفية",
};

const CHECKS: Record<PfCheck["key"], { label: string; target: string; show: (v: number | null) => string }> = {
  days: { label: "مدة التشغيل الوهمي", target: "≥ 14 يوم", show: (v) => `${formatNumber(v ?? 0, 1)} يوم` },
  max_dd: { label: "أقصى تراجع (واقعي)", target: "< 20%", show: (v) => (v === null ? "—" : `${formatNumber(v, 1)}%`) },
  liquidations: { label: "التصفيات", target: "0", show: (v) => String(v ?? 0) },
  missed_exits: { label: "إغلاقات فاتت البوت (صُححت)", target: "0", show: (v) => String(v ?? 0) },
  stale: { label: "حركات وصلت متأخرة (> دقيقتين)", target: "0", show: (v) => String(v ?? 0) },
  real_vs_base: {
    label: "تكلفة التأخير: واقعي − أساسي",
    target: "للمتابعة",
    show: (v) => (v === null ? "—" : `${v >= 0 ? "+" : ""}${formatNumber(v, 2)}%`),
  },
};

function ActionBadge({ action }: { action: string }) {
  const a = ACTION_AR[action] ?? { label: action, tone: "neutral" as const };
  return <Badge tone={a.tone}>{a.label}</Badge>;
}

function Side({ side }: { side: number }) {
  return <Badge tone={side > 0 ? "win" : "loss"}>{side > 0 ? "شراء" : "بيع"}</Badge>;
}

function WalletBadge({ wallet }: { wallet: string }) {
  return <Badge tone={wallet === "loco" ? "gold" : "info"}>{wallet === "loco" ? "Loco" : `0x${wallet}`}</Badge>;
}

function Th({ children, end }: { children?: React.ReactNode; end?: boolean }) {
  return <th className={cn("px-3 py-2 font-medium", end && "text-right")}>{children}</th>;
}

function CheckRow({ c }: { c: PfCheck }) {
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
        <span className="w-20 text-end text-xs text-muted-dim">{meta.target}</span>
      </span>
    </li>
  );
}

function EquityChart({ d }: { d: PortfolioPanel }) {
  const rt = d.runtime!;
  const start = rt.ledgers.real.start_equity;
  const yr = d.expected?.backtest_real_year ?? 1.3;
  const wf = d.expected?.walk_forward_year ?? 0.69;
  const points = (() => {
    const byT = new Map<string, Record<string, number | string>>();
    for (const p of d.curves?.real ?? []) byT.set(p.t, { t: p.t, real: p.equity });
    for (const p of d.curves?.base ?? []) byT.set(p.t, { ...(byT.get(p.t) ?? { t: p.t }), base: p.equity });
    return [...byT.values()]
      .sort((a, b) => String(a.t).localeCompare(String(b.t)))
      .map((p) => {
        const days = Math.max(0, (new Date(String(p.t)).getTime() - rt.started_ms) / DAY_MS);
        return { ...p, expected: start * Math.pow(1 + yr, days / 365), walk: start * Math.pow(1 + wf, days / 365) };
      });
  })();
  if (points.length < 2) {
    return <EmptyState icon={FlaskConical} title="المنحنى يبدأ بعد أول نقطتين" description="تُسجَّل قيمة الحسابين كل 5 دقائق." />;
  }
  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={points} margin={{ top: 10, right: 20, left: 10, bottom: 0 }}>
        <CartesianGrid stroke="#262b33" vertical={false} />
        <XAxis dataKey="t" tickFormatter={(v) => formatDateTime(v).split(",")[0]} stroke={CHART_TEXT} fontSize={11} tickLine={false} minTickGap={40} />
        <YAxis stroke={CHART_TEXT} fontSize={11} tickLine={false} width={70} domain={["auto", "auto"]} />
        <ReferenceLine y={start} stroke="#3a404b" strokeDasharray="4 4" />
        <Tooltip
          contentStyle={{ background: "#1b1f26", border: "1px solid #262b33", borderRadius: 6 }}
          labelStyle={{ color: CHART_TEXT }}
          labelFormatter={(v) => formatDateTime(v as string)}
          formatter={(value, name) => [formatUsd(value as number), name]}
        />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Line type="monotone" dataKey="real" name="واقعي" stroke={COLORS.real} strokeWidth={2} dot={false} />
        <Line type="monotone" dataKey="base" name="أساسي" stroke={COLORS.base} strokeWidth={1.5} dot={false} />
        <Line type="monotone" dataKey="expected" name="المتوقع (اختبار السنة +130%)" stroke={COLORS.expected} strokeDasharray="5 4" strokeWidth={1} dot={false} />
        <Line type="monotone" dataKey="walk" name="المتحفظ (+69%)" stroke={COLORS.walk} strokeDasharray="2 4" strokeWidth={1} dot={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

function LedgerCards({ l, label }: { l: PfLedger; label: string }) {
  return (
    <StatCard
      label={`الرصيد — ${label}`}
      value={formatUsd(l.equity)}
      tone={label === "واقعي" ? "gold" : "default"}
      sub={
        <span>
          <span className={pnlClass(l.return_pct)}>{`${l.return_pct >= 0 ? "+" : ""}${formatNumber(l.return_pct, 2)}%`}</span>
          {` · رسوم ${formatUsd(l.fees_paid)} · ${formatNumber(l.fee * 100, 3)}% لكل جهة`}
        </span>
      }
    />
  );
}

export default function PortfolioPage() {
  const [days, setDays] = useState(28);
  const [scenario, setScenario] = useState<Scenario>("real");
  const query = usePortfolio(days);
  const d = query.data;
  const rt = d?.runtime;

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="max-w-2xl">
          <h1 className="text-xl font-semibold text-bone">الثلاثي الجديد — المحاكي</h1>
          <p className="mt-1 text-sm leading-relaxed text-muted">
            ثلاث محافظ في حساب وهمي واحد بهامش مشترك ورافعة <bdi>3x</bdi>: <bdi>Loco</bdi> بوزن 50%، و<bdi>0x8477</bdi> بوزن 25%،
            و<bdi>0xc0b2</bdi> بوزن 25%. كل حركة لهم تُنسخ (فتح، تعزيز حتى سقف 20%، تقليص، إغلاق، انعكاس) في العملات الرقمية وأسواق
            الأسهم والسلع على Hyperliquid — بدون أي أمر حقيقي. «واقعي» = سعر Hyperliquid لحظة التقاط الحركة + 0.095%،
            و«أساسي» = سعر المحفظة نفسه + 0.045% (مثل الاختبار التاريخي).
          </p>
        </div>
        <div className="flex gap-3">
          <div>
            <Label>السيناريو</Label>
            <Select value={scenario} onChange={(e) => setScenario(e.target.value as Scenario)}>
              <option value="real">واقعي</option>
              <option value="base">أساسي</option>
            </Select>
          </div>
          <div>
            <Label>الفترة</Label>
            <Select value={days} onChange={(e) => setDays(Number(e.target.value))}>
              <option value={7}>آخر 7 أيام</option>
              <option value={28}>آخر 4 أسابيع</option>
              <option value={90}>آخر 90 يومًا</option>
              <option value={365}>سنة</option>
            </Select>
          </div>
        </div>
      </div>

      {query.isLoading ? (
        <LoadingState />
      ) : query.isError || !d ? (
        <ErrorState message={(query.error as Error | null)?.message ?? "—"} />
      ) : !d.enabled || !rt ? (
        <Card>
          <EmptyState
            icon={FlaskConical}
            title="المحاكي لم يبدأ بعد"
            description="يبدأ تلقائيًا عند تشغيل البوت بالإصدار الجديد (PORTFOLIO_ENABLED=true، وهو الافتراضي) — أعد تشغيل البوت على السيرفر."
          />
        </Card>
      ) : (
        <Body d={d} scenario={scenario} fetchedAt={query.dataUpdatedAt} />
      )}
    </div>
  );
}

function Body({ d, scenario, fetchedAt }: { d: PortfolioPanel; scenario: Scenario; fetchedAt: number }) {
  const rt = d.runtime!;
  const l = rt.ledgers[scenario];
  const real = rt.ledgers.real;
  const stats = d.stats!;
  const passed = d.checks?.filter((c) => c.pass).length ?? 0;
  const graded = d.checks?.filter((c) => c.pass !== null).length ?? 0;
  const stale = fetchedAt - rt.updated_ms > 120_000;
  const trades = d.trades?.[scenario] ?? [];

  return (
    <>
      {stale || real.paused_until || real.liquidations ? (
        <div className="flex flex-col gap-1.5 rounded-md border border-loss/40 bg-loss/10 px-4 py-3 text-sm text-loss-bright">
          {stale ? (
            <p className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 shrink-0" strokeWidth={1.75} />
              آخر تحديث من البوت {formatRelativeTime(new Date(rt.updated_ms).toISOString())} — تأكد أن البوت يعمل.
            </p>
          ) : null}
          {real.paused_until ? (
            <p className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 shrink-0" strokeWidth={1.75} />
              الرصيد نزل 25% من قمته: لا صفقات جديدة حتى {formatDateTime(new Date(real.paused_until).toISOString())} (الإغلاقات مستمرة).
            </p>
          ) : null}
          {real.liquidations ? (
            <p className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 shrink-0" strokeWidth={1.75} />
              تعرّض الحساب الواقعي للتصفية {real.liquidations} مرة — علامة توقف فوري حسب الخطة.
            </p>
          ) : null}
        </div>
      ) : null}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <LedgerCards l={rt.ledgers.real} label="واقعي" />
        <LedgerCards l={rt.ledgers.base} label="أساسي" />
        <StatCard
          label={`التراجع — ${SCENARIO_AR[scenario]}`}
          value={`${formatNumber(l.drawdown_pct, 1)}%`}
          tone={l.max_drawdown_pct >= 20 ? "loss" : "default"}
          sub={`الأقصى ${formatNumber(l.max_drawdown_pct, 1)}% · القمة ${formatUsd(l.peak)}`}
        />
        <StatCard
          label="غير المحقق الآن"
          value={formatMoney(l.unrealized)}
          tone={pnlTone(l.unrealized)}
          sub={`${l.positions.length} مركز مفتوح`}
        />
        <StatCard
          label="الحجم ÷ الرصيد"
          value={l.exposure_x === null ? "—" : `${formatNumber(l.exposure_x, 2)}x`}
          tone={l.exposure_x !== null && l.exposure_x > 4 ? "loss" : "default"}
          sub={`الحد 6x · ${formatUsd(l.notional)}`}
        />
        <StatCard
          label="البعد عن التصفية"
          value={l.liq_distance ? `${formatNumber(l.liq_distance, 1)}x` : "لا مراكز"}
          tone={l.liq_distance !== null && l.liq_distance < 3 ? "loss" : "default"}
          sub="الرصيد ÷ هامش الصيانة — 1x = تصفية"
        />
        <StatCard
          label="صفقات مغلقة"
          value={l.trades}
          sub={l.win_rate === null ? "لا صفقات مغلقة بعد" : `نسبة الربح ${formatNumber(l.win_rate * 100, 0)}% · المتوقع ~79%`}
        />
        <StatCard
          label="الالتقاط p50 / p95"
          value={`${formatMs(stats.latency_p50_ms)} / ${formatMs(stats.latency_p95_ms)}`}
          sub={
            stats.slippage_median_bps === null
              ? `${stats.events} حركة`
              : `فرق السعر الوسيط ${formatNumber(stats.slippage_median_bps, 1)} bps · ${stats.events} حركة`
          }
        />
      </div>

      <Card>
        <CardHeader
          title="منحنى الحسابين"
          subtitle={`بدأت المحاكاة ${formatDateTime(new Date(rt.started_ms).toISOString())} — الخطان المتقطعان: مسار +130% بالسنة (الاختبار الواقعي) و+69% (اختبار بدون معرفة المستقبل)`}
        />
        <div className="h-80 p-4 pl-0">
          <EquityChart d={d} />
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
        <Card className="lg:col-span-3">
          <CardHeader title="المحافظ" subtitle={`الحجم = K × الوزن × رصيدنا ÷ رصيد المحفظة · K = ${formatNumber(rt.settings.k, 1)} · السقف ${rt.settings.cap_pct}% هامش من الحصة`} />
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-sm">
              <thead>
                <tr className="border-b border-border-soft text-[11px] text-muted-dim">
                  <Th>المحفظة</Th>
                  <Th end>الوزن</Th>
                  <Th end>رصيدها</Th>
                  <Th end>حركات</Th>
                  <Th end>مفتوحة</Th>
                  <Th end>صفقات</Th>
                  <Th end>نسبة الربح</Th>
                  <Th end>الصافي ({SCENARIO_AR[scenario]})</Th>
                </tr>
              </thead>
              <tbody className="font-data whitespace-nowrap">
                {rt.wallets.map((w) => {
                  const s = d.by_wallet?.[w.label]?.[scenario];
                  return (
                    <tr key={w.label} className="border-b border-border-soft/60">
                      <td className="px-3 py-2 font-sans">
                        <a href={`https://hypurrscan.io/address/${w.address}`} target="_blank" rel="noreferrer" className="hover:underline">
                          <WalletBadge wallet={w.label} />
                        </a>
                      </td>
                      <td className="px-3 py-2 text-right text-bone">{formatNumber(w.weight * 100, 0)}%</td>
                      <td className="px-3 py-2 text-right text-muted">{formatUsd(w.equity)}</td>
                      <td className="px-3 py-2 text-right text-muted">{d.fills_by_wallet?.[w.label] ?? 0}</td>
                      <td className="px-3 py-2 text-right text-muted">{l.positions.filter((p) => p.wallet === w.label).length}</td>
                      <td className="px-3 py-2 text-right text-bone">{s?.trades ?? 0}</td>
                      <td className="px-3 py-2 text-right text-muted">{s?.trades ? `${formatNumber((s.wins / s.trades) * 100, 0)}%` : "—"}</td>
                      <td className={cn("px-3 py-2 text-right", pnlClass(s?.pnl ?? 0))}>{formatMoney(s?.pnl ?? 0)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="border-t border-border-soft px-4 py-2.5 text-xs leading-relaxed text-muted-dim">
            المراكز التي كانت مفتوحة عند بدء المحاكاة لا تُنسخ — ننتظر حتى تُغلق، ثم ننسخ الصفقة التالية.
          </p>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader title="شروط الانتقال للحقيقي" subtitle={`الخطة §9 — ${passed} / ${graded} ناجحة`} />
          <ul className="px-4 py-1">{d.checks?.map((c) => <CheckRow key={c.key} c={c} />)}</ul>
        </Card>
      </div>

      <Card>
        <CardHeader title={`المراكز المفتوحة — ${SCENARIO_AR[scenario]}`} subtitle="حصة كل محفظة مستقلة؛ نفس العملة من محفظتين تظهر في سطرين" />
        {!l.positions.length ? (
          <EmptyState icon={FlaskConical} title="لا مراكز مفتوحة الآن" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[960px] text-left text-sm">
              <thead>
                <tr className="border-b border-border-soft text-[11px] text-muted-dim">
                  <Th>المحفظة</Th>
                  <Th>العملة</Th>
                  <Th>الاتجاه</Th>
                  <Th end>الحجم $</Th>
                  <Th end>الدخول</Th>
                  <Th end>السعر</Th>
                  <Th end>غير محقق</Th>
                  <Th end>السقف المستخدم</Th>
                  <Th end>تعزيزات</Th>
                  <Th>منذ</Th>
                </tr>
              </thead>
              <tbody className="font-data whitespace-nowrap">
                {l.positions.map((p) => (
                  <tr key={`${p.wallet}|${p.coin}`} className="border-b border-border-soft/60">
                    <td className="px-3 py-2 font-sans"><WalletBadge wallet={p.wallet} /></td>
                    <td className="px-3 py-2 text-bone">{p.coin}</td>
                    <td className="px-3 py-2"><Side side={p.side} /></td>
                    <td className="px-3 py-2 text-right text-bone">{formatUsd(p.notional)}</td>
                    <td className="px-3 py-2 text-right text-muted">{formatPrice(p.entry)}</td>
                    <td className="px-3 py-2 text-right text-muted">{formatPrice(p.mark)}</td>
                    <td className={cn("px-3 py-2 text-right", pnlClass(p.upnl))}>{formatMoney(p.upnl)}</td>
                    <td className={cn("px-3 py-2 text-right", p.capped ? "text-warn" : "text-muted")}>
                      {p.cap ? `${formatNumber(((p.qty * p.entry) / p.cap) * 100, 0)}%` : "—"}
                    </td>
                    <td className="px-3 py-2 text-right text-muted">{p.adds}</td>
                    <td className="px-3 py-2 text-muted">{p.opened_ms ? formatRelativeTime(new Date(p.opened_ms).toISOString()) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card>
        <CardHeader title={`الصفقات المغلقة — ${SCENARIO_AR[scenario]}`} subtitle="الصافي بعد الرسوم، آخر 80 صفقة في الفترة" />
        {!trades.length ? (
          <EmptyState icon={FlaskConical} title="لا صفقات مغلقة في الفترة" description="المتوقع نحو 5 صفقات مغلقة في اليوم للثلاثي معًا." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-left text-sm">
              <thead>
                <tr className="border-b border-border-soft text-[11px] text-muted-dim">
                  <Th>أُغلقت</Th>
                  <Th>المحفظة</Th>
                  <Th>العملة</Th>
                  <Th>الاتجاه</Th>
                  <Th end>أعلى حجم $</Th>
                  <Th end>المدة</Th>
                  <Th end>تعزيزات</Th>
                  <Th end>الصافي</Th>
                  <Th>السبب</Th>
                </tr>
              </thead>
              <tbody className="font-data whitespace-nowrap">
                {trades.map((t) => (
                  <tr key={t.id} className="border-b border-border-soft/60">
                    <td className="px-3 py-2 text-muted">{formatDateTime(t.closed)}</td>
                    <td className="px-3 py-2 font-sans"><WalletBadge wallet={t.wallet} /></td>
                    <td className="px-3 py-2 text-bone">{t.coin}</td>
                    <td className="px-3 py-2"><Side side={t.side} /></td>
                    <td className="px-3 py-2 text-right text-muted">{formatUsd(t.max_notional)}</td>
                    <td className="px-3 py-2 text-right text-muted">
                      {t.opened && t.closed ? formatDuration(new Date(t.closed).getTime() - new Date(t.opened).getTime()) : "—"}
                    </td>
                    <td className="px-3 py-2 text-right text-muted">{t.adds}</td>
                    <td className={cn("px-3 py-2 text-right", pnlClass(t.pnl ?? 0))}>{t.pnl === null ? "—" : formatMoney(t.pnl)}</td>
                    <td className="px-3 py-2 font-sans text-xs text-muted">{REASON_AR[t.reason] ?? t.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card>
        <CardHeader title="آخر حركات المحافظ" subtitle="كل تنفيذ للمحفظة، وما فعله كل سيناريو به — سعرها مقابل سعر Hyperliquid لحظة التقاطه" />
        {!d.recent?.length ? (
          <EmptyState icon={FlaskConical} title="لا حركات بعد" description="أول حركة تظهر هنا خلال ثوانٍ من تنفيذها." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1040px] text-left text-sm">
              <thead>
                <tr className="border-b border-border-soft text-[11px] text-muted-dim">
                  <Th>الوقت</Th>
                  <Th>المحفظة</Th>
                  <Th>العملة</Th>
                  <Th>الجهة</Th>
                  <Th end>الكمية</Th>
                  <Th end>مركزها بعدها</Th>
                  <Th end>سعرها</Th>
                  <Th end>سعرنا</Th>
                  <Th end>الفرق</Th>
                  <Th end>الالتقاط</Th>
                  <Th>واقعي</Th>
                  <Th>أساسي</Th>
                </tr>
              </thead>
              <tbody className="font-data whitespace-nowrap">
                {d.recent.map((e) => (
                  <tr key={e.id} className="border-b border-border-soft/60">
                    <td className="px-3 py-2 text-muted">{formatDateTime(e.detected_at)}</td>
                    <td className="px-3 py-2 font-sans"><WalletBadge wallet={e.wallet} /></td>
                    <td className="px-3 py-2 text-bone">{e.coin}</td>
                    <td className="px-3 py-2"><Side side={e.side === "B" ? 1 : -1} /></td>
                    <td className="px-3 py-2 text-right text-muted">{formatNumber(e.sz, 4)}</td>
                    <td className="px-3 py-2 text-right text-muted">{formatNumber(e.start_pos + (e.side === "B" ? e.sz : -e.sz), 4)}</td>
                    <td className="px-3 py-2 text-right text-bone">{formatPrice(e.px)}</td>
                    <td className="px-3 py-2 text-right text-bone">{formatPrice(e.real_px)}</td>
                    <td className={cn("px-3 py-2 text-right", (e.slippage_bps ?? 0) > 10 ? "text-warn" : "text-muted")}>
                      {e.slippage_bps === null ? "—" : `${formatNumber(e.slippage_bps, 1)} bps`}
                    </td>
                    <td className={cn("px-3 py-2 text-right", e.lag_ms > 10_000 ? "text-warn" : "text-muted")}>{formatMs(e.lag_ms)}</td>
                    <td className="px-3 py-2"><ActionBadge action={e.real_action} /></td>
                    <td className="px-3 py-2"><ActionBadge action={e.base_action} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <p className="text-xs leading-relaxed text-muted-dim">
        محاكاة فقط — لا يُرسل أي أمر. التمويل (funding) غير محسوب، مثل الاختبار التاريخي. الأسعار من Hyperliquid (أسعار الوسط
        كل ثوانٍ عبر WebSocket). ليست نصيحة مالية.
      </p>
    </>
  );
}
