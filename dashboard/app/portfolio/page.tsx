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
const COLORS = { real: "#c9a227", base: "#5b8def" };
const EXPECTED_COLORS = ["#6b7280", "#3a404b", "#4b5563"];
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

/** Why a fill did what it did -- every case the ledger can return (pf_ledger.Outcome). */
const ACTION_WHY: Record<string, string> = {
  OPEN: "نُسخت: فُتحت صفقة",
  ADD: "نُسخت: عُزّزت الصفقة",
  REDUCE: "نُسخت: قُلّصت بنفس نسبة المتداول",
  CLOSE: "نُسخت: أُغلقت مع المتداول",
  FLIP: "نُسخت: أُغلقت وفُتحت بالاتجاه المعاكس",
  PENDING: "لم تُنفّذ بعد: الحجم أقل من $10 — يُجمع مع التعزيز التالي",
  CAPPED: "رُفضت: الصفقة بلغت سقف 20% من الرصيد",
  EXPOSURE: "رُفضت: الحجم الكلي سيتجاوز 6 أضعاف الرصيد",
  WAIT: "لم تُنسخ: مركز فتحه المتداول قبل بدء المحاكي — يُنتظر حتى يغلقه",
  IGNORED: "لا شيء: لا نحمل حصة في هذه الصفقة",
  SKIP_PAUSED: "رُفضت: الحساب نزل 25% من قمته — لا صفقات جديدة 7 أيام",
  SKIP_STALE: "رُفضت: وصلت الحركة بعد أكثر من دقيقتين",
  SKIP_NO_EQUITY: "رُفضت: رصيد المتداول غير معروف — لا يمكن حساب الحجم",
};

const ORDER_AR: Record<string, { label: string; tone: "gold" | "win" | "loss" | "warn" | "info" | "neutral" }> = {
  OPEN: { label: "فتح صفقة", tone: "gold" },
  ADD: { label: "تعزيز", tone: "win" },
  REDUCE: { label: "تقليص", tone: "info" },
  CLOSE: { label: "إغلاق", tone: "info" },
  FLIP_CLOSE: { label: "انعكاس — إغلاق", tone: "info" },
  FLIP_OPEN: { label: "انعكاس — فتح", tone: "gold" },
  RECONCILED: { label: "إغلاق تصحيحي", tone: "warn" },
  LIQUIDATED: { label: "تصفية", tone: "loss" },
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
  return (
    <span title={ACTION_WHY[action]}>
      <Badge tone={a.tone}>{a.label}</Badge>
    </span>
  );
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
  const start = d.ledger!.real.start_equity;
  const expected = d.expected ?? [];
  const points = (() => {
    const byT = new Map<string, Record<string, number | string>>();
    for (const p of d.curves?.real ?? []) byT.set(p.t, { t: p.t, real: p.equity });
    for (const p of d.curves?.base ?? []) byT.set(p.t, { ...(byT.get(p.t) ?? { t: p.t }), base: p.equity });
    return [...byT.values()]
      .sort((a, b) => String(a.t).localeCompare(String(b.t)))
      .map((p) => {
        const days = Math.max(0, (new Date(String(p.t)).getTime() - rt.started_ms) / DAY_MS);
        const ref: Record<string, number> = {};
        expected.forEach((x, i) => {
          ref[`exp${i}`] = start * Math.pow(x.year, days / 365);
        });
        return { ...p, ...ref };
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
        {expected.map((x, i) => (
          <Line
            key={x.label}
            type="monotone"
            dataKey={`exp${i}`}
            name={x.label}
            stroke={EXPECTED_COLORS[i % EXPECTED_COLORS.length]}
            strokeDasharray={i === 0 ? "5 4" : "2 4"}
            strokeWidth={1}
            dot={false}
          />
        ))}
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
          {` · رسوم مخصومة ${formatUsd(l.fees_paid)} (${formatNumber(l.fee * 100, 3)}% لكل أمر)`}
        </span>
      }
    />
  );
}

export default function PortfolioPage() {
  const [days, setDays] = useState(28);
  const [scenario, setScenario] = useState<Scenario>("real");
  const [capital, setCapital] = useState<number | undefined>(undefined);
  const query = usePortfolio(days, capital);
  const d = query.data;
  const rt = d?.runtime;

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="max-w-2xl">
          <h1 className="text-xl font-semibold text-bone">محاكي الثلاثي على Hyperliquid</h1>
          <p className="mt-1 text-sm leading-relaxed text-muted">
            المتداولون الثلاثة <bdi>8bae</bdi> و<bdi>8fc7</bdi> و<bdi>69b0</bdi> في حساب وهمي واحد بهامش مشترك ورافعة <bdi>3x</bdi>،
            وكل واحد يُحسب حجمه على رصيد الحساب كاملًا (كما في خطة v5). كل حركة لهم تُنسخ (فتح، تعزيز حتى سقف 20%، تقليص، إغلاق،
            انعكاس) في العملات الرقمية وأسواق الأسهم والسلع على Hyperliquid — بدون أي أمر حقيقي. ثلاثة حسابات بنفس الحركات:
            ‏$10,000 و$50,000 و$100,000. «واقعي» = سعر Hyperliquid لحظة التقاط الحركة + 0.095%، و«أساسي» = سعر المتداول نفسه + 0.045%.
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
      ) : !d.ledger?.real || !d.ledger?.base || !d.stats ? (
        // The page and the server's API are from different versions (the page was published before the bot was
        // updated): say so instead of crashing on the missing fields.
        <Card>
          <EmptyState
            icon={FlaskConical}
            title="البوت على السيرفر بإصدار أقدم من هذه الصفحة"
            description="حدّث ملفات البوت على السيرفر وأعد تشغيله وتشغيل واجهة اللوحة، ثم حدّث الصفحة."
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
  const l = d.ledger![scenario];
  const real = d.ledger!.real;
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
        <LedgerCards l={d.ledger!.real} label="واقعي" />
        <LedgerCards l={d.ledger!.base} label="أساسي" />
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
          sub={l.win_rate === null ? "لا صفقات مغلقة بعد" : `نسبة الربح ${formatNumber(l.win_rate * 100, 0)}% · الاختبار ~61%`}
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
        <CardHeader title="الحسابات الثلاثة" subtitle="نفس الحركات على ثلاث أرصدة بداية — اختر رأس المال من أعلى الصفحة لتفاصيله" />
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead>
              <tr className="border-b border-border-soft text-[11px] text-muted-dim">
                <Th>رأس المال</Th>
                <Th end>واقعي</Th>
                <Th end>%</Th>
                <Th end>أساسي</Th>
                <Th end>%</Th>
                <Th end>أقصى تراجع (واقعي)</Th>
                <Th end>صفقات مغلقة</Th>
                <Th end>مفتوحة</Th>
                <Th end>الحجم ÷ الرصيد</Th>
              </tr>
            </thead>
            <tbody className="font-data whitespace-nowrap">
              {(d.summary ?? []).map((row) => (
                <tr key={row.capital} className={cn("border-b border-border-soft/60", row.capital === d.capital && "bg-gold/5")}>
                  <td className="px-3 py-2 text-bone">{formatUsd(row.capital)}</td>
                  <td className="px-3 py-2 text-right text-bone">{formatUsd(row.real.equity ?? null)}</td>
                  <td className={cn("px-3 py-2 text-right", pnlClass(row.real.return_pct ?? 0))}>{`${formatNumber(row.real.return_pct ?? 0, 2)}%`}</td>
                  <td className="px-3 py-2 text-right text-muted">{formatUsd(row.base.equity ?? null)}</td>
                  <td className={cn("px-3 py-2 text-right", pnlClass(row.base.return_pct ?? 0))}>{`${formatNumber(row.base.return_pct ?? 0, 2)}%`}</td>
                  <td className="px-3 py-2 text-right text-muted">{`${formatNumber(row.real.max_drawdown_pct ?? 0, 1)}%`}</td>
                  <td className="px-3 py-2 text-right text-muted">{row.real.trades ?? 0}</td>
                  <td className="px-3 py-2 text-right text-muted">{row.real.open_trades ?? 0}</td>
                  <td className="px-3 py-2 text-right text-muted">{row.real.exposure_x === null || row.real.exposure_x === undefined ? "—" : `${formatNumber(row.real.exposure_x, 2)}x`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card>
        <CardHeader
          title="منحنى الحسابين"
          subtitle={`${formatUsd(d.capital)} — بدأت المحاكاة ${formatDateTime(new Date(rt.started_ms).toISOString())} — الخطوط المتقطعة: مسار نتيجة الاختبار التاريخي (botup §V5.0 / §V5.4)`}
        />
        <div className="h-80 p-4 pl-0">
          <EquityChart d={d} />
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
        <Card className="lg:col-span-3">
          <CardHeader title="المتداولون" subtitle={`الحجم = K × الوزن × رصيدنا ÷ رصيد المتداول · K = ${formatNumber(rt.settings.k, 1)} · السقف ${rt.settings.cap_pct}% هامش من الرصيد لكل صفقة`} />
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-sm">
              <thead>
                <tr className="border-b border-border-soft text-[11px] text-muted-dim">
                  <Th>المتداول</Th>
                  <Th end>الوزن</Th>
                  <Th end>رصيده</Th>
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
            المراكز التي كانت مفتوحة عند بدء المحاكاة لا تُنسخ — ننتظر حتى تُغلق، ثم ننسخ الصفقة التالية. متداول هادئ لأسابيع أمر طبيعي.
          </p>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader title="شروط الانتقال للحقيقي" subtitle={`botup §V6.8 — ${passed} / ${graded} ناجحة`} />
          <ul className="px-4 py-1">{d.checks?.map((c) => <CheckRow key={c.key} c={c} />)}</ul>
        </Card>
      </div>

      <Card>
        <CardHeader title={`المراكز المفتوحة — ${SCENARIO_AR[scenario]}`} subtitle="حصة كل متداول مستقلة؛ نفس العملة من متداولَين تظهر في سطرين" />
        {!l.positions.length ? (
          <EmptyState icon={FlaskConical} title="لا مراكز مفتوحة الآن" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[960px] text-left text-sm">
              <thead>
                <tr className="border-b border-border-soft text-[11px] text-muted-dim">
                  <Th>المتداول</Th>
                  <Th>العملة</Th>
                  <Th>الاتجاه</Th>
                  <Th end>الحجم $</Th>
                  <Th end>الدخول</Th>
                  <Th end>السعر</Th>
                  <Th end>غير محقق</Th>
                  <Th end>الرسوم</Th>
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
                    <td className="px-3 py-2 text-right text-muted">{formatMoney(-p.fees)}</td>
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
        <CardHeader
          title={`سجل صفقاتنا — ${SCENARIO_AR[scenario]} · ${formatUsd(d.capital)}`}
          subtitle={`كل أمر نفّذه الحساب الوهمي بعد حركة من الثلاثي، مع الرسوم المخصومة منه — مجموع الرسوم في الفترة ${formatUsd(d.fees_in_period?.[scenario] ?? 0)}`}
        />
        {!(d.orders?.[scenario] ?? []).length ? (
          <EmptyState icon={FlaskConical} title="لا أوامر بعد" description="أول ما يفتح أحد الثلاثة صفقة جديدة، تُفتح هنا خلال ثوانٍ وتُسجَّل." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-left text-sm">
              <thead>
                <tr className="border-b border-border-soft text-[11px] text-muted-dim">
                  <Th>الوقت</Th>
                  <Th>المتداول</Th>
                  <Th>العملة</Th>
                  <Th>العملية</Th>
                  <Th>الجهة</Th>
                  <Th end>الكمية</Th>
                  <Th end>السعر</Th>
                  <Th end>الحجم $</Th>
                  <Th end>الرسوم</Th>
                </tr>
              </thead>
              <tbody className="font-data whitespace-nowrap">
                {(d.orders?.[scenario] ?? []).slice(0, 100).map((o) => {
                  const k = ORDER_AR[o.kind] ?? { label: o.kind, tone: "neutral" as const };
                  return (
                    <tr key={o.id} className="border-b border-border-soft/60">
                      <td className="px-3 py-2 text-muted">{formatDateTime(o.time)}</td>
                      <td className="px-3 py-2 font-sans"><WalletBadge wallet={o.wallet} /></td>
                      <td className="px-3 py-2 text-bone">{o.coin}</td>
                      <td className="px-3 py-2 font-sans"><Badge tone={k.tone}>{k.label}</Badge></td>
                      <td className="px-3 py-2"><Side side={o.qty > 0 ? 1 : -1} /></td>
                      <td className="px-3 py-2 text-right text-muted">{formatNumber(Math.abs(o.qty), 6)}</td>
                      <td className="px-3 py-2 text-right text-bone">{formatPrice(o.px)}</td>
                      <td className="px-3 py-2 text-right text-bone">{formatUsd(o.notional)}</td>
                      <td className="px-3 py-2 text-right text-loss-bright">{formatMoney(-o.fee)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card>
        <CardHeader title={`الصفقات المغلقة — ${SCENARIO_AR[scenario]}`} subtitle="الصافي بعد خصم رسوم الفتح والإغلاق والتعزيزات، آخر 80 صفقة في الفترة" />
        {!trades.length ? (
          <EmptyState icon={FlaskConical} title="لا صفقات مغلقة في الفترة" description="المتوقع نحو 5 صفقات جديدة في الأسبوع للثلاثي معًا (botup §V5.2)." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-left text-sm">
              <thead>
                <tr className="border-b border-border-soft text-[11px] text-muted-dim">
                  <Th>أُغلقت</Th>
                  <Th>المتداول</Th>
                  <Th>العملة</Th>
                  <Th>الاتجاه</Th>
                  <Th end>أعلى حجم $</Th>
                  <Th end>المدة</Th>
                  <Th end>تعزيزات</Th>
                  <Th end>الرسوم</Th>
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
                    <td className="px-3 py-2 text-right text-muted">{formatMoney(-t.fees)}</td>
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
        <CardHeader title="آخر حركات المحافظ" subtitle="كل تنفيذ للمتداول، وما فعله كل سيناريو به في حساب $10,000 — سعره مقابل سعر Hyperliquid لحظة التقاطه" />
        {!d.recent?.length ? (
          <EmptyState icon={FlaskConical} title="لا حركات بعد" description="أول حركة تظهر هنا خلال ثوانٍ من تنفيذها." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1320px] text-left text-sm">
              <thead>
                <tr className="border-b border-border-soft text-[11px] text-muted-dim">
                  <Th>الوقت</Th>
                  <Th>المتداول</Th>
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
                  <Th>السبب (واقعي)</Th>
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
                    <td dir="rtl" className={cn("px-3 py-2 text-start font-sans text-xs",
                      /^(رُفضت|لم)/.test(ACTION_WHY[e.real_action] ?? "") ? "text-warn" : "text-muted")}>
                      {ACTION_WHY[e.real_action] ?? e.real_action}
                    </td>
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
