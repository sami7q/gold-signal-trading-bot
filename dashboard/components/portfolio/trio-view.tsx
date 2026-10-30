"use client";

// The trio simulator, simplified (owner's ask 2026-10-30): open trades as cards (total size on the chosen capital,
// live P&L, and the trader's real position next to ours), a chart per trade side by side, the history, and what
// the bot would have done while it was stopped. Everything else of the full simulator page is left out here.

import { Fragment, useMemo, useState } from "react";
import { ChevronDown, ChevronUp, FlaskConical, LineChart, PauseCircle } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/card";
import { StatCard } from "@/components/ui/stat-card";
import { Badge } from "@/components/ui/badge";
import { Label, Select } from "@/components/ui/input";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/empty-state";
import { LiveTradeChart } from "@/components/trade/live-chart";
import { useOutageSim, usePortfolio } from "@/hooks/use-loco";
import { useLivePrices } from "@/hooks/use-live-prices";
import { useWalletPositions, type WalletPosition } from "@/hooks/use-wallet-positions";
import type { Book } from "@/lib/hl-live";
import {
  cn,
  formatDateTime,
  formatDuration,
  formatMoney,
  formatNumber,
  formatPrice,
  formatRelativeTime,
  formatUsd,
  pnlClass,
  pnlTone,
} from "@/lib/format";
import type { PfOrder, PfPosition, PfTrade, PortfolioPanel } from "@/types/api";

const GROUP = "trio";
const HISTORY_DAYS = 90;

const ORDER_AR: Record<string, { label: string; tone: "gold" | "win" | "loss" | "warn" | "info" | "neutral" }> = {
  OPEN: { label: "فتح", tone: "gold" },
  ADD: { label: "تعزيز", tone: "win" },
  REDUCE: { label: "تقليص", tone: "info" },
  CLOSE: { label: "إغلاق", tone: "info" },
  FLIP_CLOSE: { label: "انعكاس — إغلاق", tone: "info" },
  FLIP_OPEN: { label: "انعكاس — فتح", tone: "gold" },
  RECONCILED: { label: "إغلاق تصحيحي", tone: "warn" },
  LIQUIDATED: { label: "تصفية", tone: "loss" },
};

type LivePos = PfPosition & { live: boolean };
type OrderRow = Pick<PfOrder, "kind" | "qty" | "px" | "notional" | "fee" | "ts">;

function livePos(p: PfPosition, books: Record<string, Book>): LivePos {
  const b = books[p.coin];
  if (!b) return { ...p, live: false };
  return { ...p, mark: b.mid, upnl: p.side * p.qty * (b.mid - p.entry), notional: p.qty * b.mid, live: true };
}

const keyOf = (p: { wallet: string; coin: string }) => `${p.wallet}|${p.coin}`;
const signedPct = (v: number) => `${v >= 0 ? "+" : ""}${formatNumber(v, 2)}%`;

function Side({ side }: { side: number }) {
  return <Badge tone={side > 0 ? "win" : "loss"}>{side > 0 ? "شراء" : "بيع"}</Badge>;
}

function Wallet({ wallet }: { wallet: string }) {
  return <Badge tone="info">{`0x${wallet}`}</Badge>;
}

function LiveDot() {
  return <span className="pulse ms-1.5 inline-block h-1.5 w-1.5 rounded-full bg-win-bright align-middle" title="سعر لحظي من Hyperliquid" />;
}

function Row({ k, children, className }: { k: string; children: React.ReactNode; className?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <span className="text-xs text-muted">{k}</span>
      <span className={cn("font-data text-sm text-bone", className)}>{children}</span>
    </div>
  );
}

/** Every order of one trade: the open, each add, each reduce and the close. */
function OrdersTable({ orders }: { orders: OrderRow[] }) {
  if (!orders.length) return <p className="px-4 py-3 text-xs text-muted-dim">لا أوامر مسجّلة لهذه الصفقة في الفترة.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[560px] text-left text-sm">
        <thead>
          <tr className="text-[11px] text-muted-dim">
            <th className="px-3 py-1.5 font-medium">الوقت</th>
            <th className="px-3 py-1.5 font-medium">العملية</th>
            <th className="px-3 py-1.5 text-right font-medium">الكمية</th>
            <th className="px-3 py-1.5 text-right font-medium">السعر</th>
            <th className="px-3 py-1.5 text-right font-medium">الحجم</th>
            <th className="px-3 py-1.5 text-right font-medium">الرسوم</th>
          </tr>
        </thead>
        <tbody className="font-data whitespace-nowrap">
          {orders.map((o, i) => {
            const k = ORDER_AR[o.kind] ?? { label: o.kind, tone: "neutral" as const };
            return (
              <tr key={`${o.ts}-${i}`} className="border-t border-border-soft">
                <td className="px-3 py-1.5 text-muted">{formatDateTime(new Date(o.ts).toISOString())}</td>
                <td className="px-3 py-1.5 font-sans"><Badge tone={k.tone}>{k.label}</Badge></td>
                <td className="px-3 py-1.5 text-right text-muted">{formatNumber(Math.abs(o.qty), 6)}</td>
                <td className="px-3 py-1.5 text-right text-bone">{formatPrice(o.px)}</td>
                <td className="px-3 py-1.5 text-right text-bone">{formatUsd(o.notional)}</td>
                <td className="px-3 py-1.5 text-right text-loss-bright">{formatMoney(-o.fee)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function PositionCard({
  p,
  equity,
  real,
  realLoading,
  orders,
  charted,
  onChart,
}: {
  p: LivePos;
  equity: number;
  real: WalletPosition | undefined;
  realLoading: boolean;
  orders: OrderRow[];
  charted: boolean;
  onChart: () => void;
}) {
  const [open, setOpen] = useState(false);
  const pct = p.entry ? p.side * (p.mark / p.entry - 1) * 100 : 0;
  const realSame = real && real.size !== 0 && Math.sign(real.size) === p.side;
  return (
    <div className="glass flex flex-col rounded-2xl">
      <button type="button" onClick={() => setOpen((x) => !x)} className="flex items-center justify-between gap-3 px-4 pt-4 text-start">
        <span className="flex items-center gap-2">
          <span className="font-data text-lg font-semibold text-bone">{p.coin}</span>
          <Side side={p.side} />
          <Wallet wallet={p.wallet} />
        </span>
        <span className="flex items-center gap-2 text-xs text-muted">
          {p.opened_ms ? `منذ ${formatRelativeTime(new Date(p.opened_ms).toISOString())}` : null}
          {open ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
        </span>
      </button>

      <div className="flex items-end justify-between gap-3 px-4 pt-3">
        <div>
          <div className="text-[11px] text-muted">الحجم الكلي على رأس مالك</div>
          <div className="font-data text-2xl font-medium text-bone">{formatUsd(p.notional)}</div>
          <div className="text-xs text-muted-dim">{equity > 0 ? `${formatNumber((p.notional / equity) * 100, 1)}% من رصيدك` : null}</div>
        </div>
        <div className="text-end">
          <div className="text-[11px] text-muted">الربح / الخسارة الآن{p.live ? <LiveDot /> : null}</div>
          <div className={cn("font-data text-2xl font-medium", pnlClass(p.upnl))}>{formatMoney(p.upnl)}</div>
          <div className={cn("font-data text-xs", pnlClass(pct))}>{signedPct(pct)}</div>
        </div>
      </div>

      <div className="mt-3 grid grid-cols-1 gap-x-6 border-t border-border-soft px-4 py-3 sm:grid-cols-2">
        <div>
          <div className="mb-1 text-[11px] font-medium text-gold-bright">حسابك (محاكاة)</div>
          <Row k="الدخول">{formatPrice(p.entry)}</Row>
          <Row k="السعر">{formatPrice(p.mark)}</Row>
          <Row k="التعزيزات">{p.adds}</Row>
          <Row k="الرسوم" className="text-loss-bright">{formatMoney(-p.fees)}</Row>
        </div>
        <div className="mt-3 border-t border-border-soft pt-3 sm:mt-0 sm:border-t-0 sm:border-s sm:ps-6 sm:pt-0">
          <div className="mb-1 text-[11px] font-medium text-info">الصفقة الحقيقية بمحفظة المتداول</div>
          {realSame ? (
            <>
              <Row k="حجمها">{formatUsd(real.notional)}</Row>
              <Row k="الكمية">{formatNumber(Math.abs(real.size), 4)}</Row>
              <Row k="دخوله">{formatPrice(real.entry)}</Row>
              <Row k="ربحه الآن" className={pnlClass(real.upnl)}>{formatMoney(real.upnl)}</Row>
              <Row k="رافعته">{real.leverage ? `${real.leverage}x` : "—"}</Row>
            </>
          ) : (
            <p className="py-2 text-xs text-muted-dim">
              {realLoading ? "تحميل من Hyperliquid…" : "لا مركز بهذا الاتجاه بمحفظته الآن — أغلقه أو قلبه، والبوت يلحقه."}
            </p>
          )}
        </div>
      </div>
      {realSame && real.notional > 0 ? (
        <p className="px-4 pb-2 text-[11px] text-muted-dim">
          حجمنا = {formatNumber((p.notional / real.notional) * 100, 3)}% من حجم صفقته
        </p>
      ) : null}

      <div className="flex gap-2 border-t border-border-soft px-4 py-2.5">
        <button
          type="button"
          onClick={() => setOpen((x) => !x)}
          className="rounded-xl px-3 py-1.5 text-xs text-muted hover:bg-white/10 hover:text-bone"
        >
          {open ? "إخفاء التفاصيل" : "التفاصيل والتعزيزات"}
        </button>
        <button
          type="button"
          onClick={onChart}
          className={cn(
            "flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-xs",
            charted ? "bg-gold/15 text-gold-bright" : "text-muted hover:bg-white/10 hover:text-bone",
          )}
        >
          <LineChart className="h-3.5 w-3.5" strokeWidth={1.75} />
          {charted ? "الشارت مفتوح" : "افتح الشارت"}
        </button>
      </div>
      {open ? (
        <div className="border-t border-border-soft pb-2">
          <OrdersTable orders={orders} />
        </div>
      ) : null}
    </div>
  );
}

function OpenTab({ d, books, capital }: { d: PortfolioPanel; books: Record<string, Book>; capital: number }) {
  const real = d.ledger!.real;
  const positions = real.positions.map((p) => livePos(p, books));
  const equity = real.cash + positions.reduce((a, p) => a + p.upnl, 0);
  const addresses = Object.fromEntries((d.runtime?.wallets ?? []).map((w) => [w.label, w.address]));
  const walletQ = useWalletPositions(positions.filter((p) => addresses[p.wallet]).map((p) => ({ address: addresses[p.wallet], coin: p.coin })));
  const [picked, setPicked] = useState<string[] | null>(null);
  const charts = (picked ?? (positions[0] ? [keyOf(positions[0])] : [])).filter((k) => positions.some((p) => keyOf(p) === k));
  const toggle = (k: string) => setPicked(charts.includes(k) ? charts.filter((x) => x !== k) : [...charts, k]);
  const ordersOf = (p: PfPosition): OrderRow[] =>
    (d.orders?.real ?? [])
      .filter((o) => o.wallet === p.wallet && o.coin === p.coin && o.ts >= (p.opened_ms ?? 0))
      .sort((a, b) => a.ts - b.ts);

  if (!positions.length) {
    return (
      <Card>
        <EmptyState
          icon={FlaskConical}
          title="لا صفقات مفتوحة الآن"
          description="أول ما يفتح أحد الثلاثة صفقة جديدة، تظهر هنا خلال ثوانٍ بحجمها على رأس مالك وحجمها الحقيقي بمحفظته."
        />
      </Card>
    );
  }
  return (
    <>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {positions.map((p) => (
          <PositionCard
            key={keyOf(p)}
            p={p}
            equity={equity}
            real={addresses[p.wallet] ? walletQ.data?.[`${addresses[p.wallet]}|${p.coin}`] : undefined}
            realLoading={walletQ.isLoading}
            orders={ordersOf(p)}
            charted={charts.includes(keyOf(p))}
            onChart={() => toggle(keyOf(p))}
          />
        ))}
      </div>
      {charts.length ? (
        <div className={cn("grid grid-cols-1 gap-4", charts.length > 1 && "2xl:grid-cols-2")}>
          {charts.map((k) => {
            const [wallet, coin] = k.split("|");
            return (
              <Card key={k} className="overflow-hidden">
                <LiveTradeChart
                  wallet={wallet}
                  coin={coin}
                  capital={capital}
                  scenario="real"
                  group={GROUP}
                  leverage={d.leverage}
                  compact={charts.length > 1}
                  onClose={() => toggle(k)}
                />
              </Card>
            );
          })}
        </div>
      ) : null}
    </>
  );
}

/** Closed trades; a row opens every order of that trade (the open, the adds, the reduces, the close). */
function TradesTable({ trades, orders }: { trades: (Omit<PfTrade, "id"> & { id?: number })[]; orders: (OrderRow & { wallet: string; coin: string })[] }) {
  const [open, setOpen] = useState<string | null>(null);
  if (!trades.length) return <EmptyState icon={FlaskConical} title="لا صفقات مغلقة بعد" />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[820px] text-left text-sm">
        <thead>
          <tr className="border-b border-border-soft text-[11px] text-muted-dim">
            <th className="px-3 py-2 font-medium">أُغلقت</th>
            <th className="px-3 py-2 font-medium">المتداول</th>
            <th className="px-3 py-2 font-medium">العملة</th>
            <th className="px-3 py-2 font-medium">الاتجاه</th>
            <th className="px-3 py-2 text-right font-medium">أعلى حجم</th>
            <th className="px-3 py-2 text-right font-medium">المدة</th>
            <th className="px-3 py-2 text-right font-medium">تعزيزات</th>
            <th className="px-3 py-2 text-right font-medium">الصافي</th>
            <th className="w-8" />
          </tr>
        </thead>
        <tbody className="font-data whitespace-nowrap">
          {trades.map((t, i) => {
            const k = `${t.wallet}|${t.coin}|${t.closed}|${i}`;
            const o = new Date(t.opened ?? 0).getTime();
            const c = new Date(t.closed ?? 0).getTime();
            const mine = orders.filter((x) => x.wallet === t.wallet && x.coin === t.coin && x.ts >= o && x.ts <= c).sort((a, b) => a.ts - b.ts);
            return (
              <Fragment key={k}>
                <tr onClick={() => setOpen(open === k ? null : k)} className="cursor-pointer border-b border-border-soft hover:bg-white/[0.04]">
                  <td className="px-3 py-2 text-muted">{formatDateTime(t.closed)}</td>
                  <td className="px-3 py-2 font-sans"><Wallet wallet={t.wallet} /></td>
                  <td className="px-3 py-2 text-bone">{t.coin}</td>
                  <td className="px-3 py-2"><Side side={t.side} /></td>
                  <td className="px-3 py-2 text-right text-bone">{formatUsd(t.max_notional)}</td>
                  <td className="px-3 py-2 text-right text-muted">{t.opened && t.closed ? formatDuration(c - o) : "—"}</td>
                  <td className="px-3 py-2 text-right text-muted">{t.adds}</td>
                  <td className={cn("px-3 py-2 text-right", pnlClass(t.pnl ?? 0))}>{t.pnl === null ? "—" : formatMoney(t.pnl)}</td>
                  <td className="px-2 py-2 text-muted">{open === k ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}</td>
                </tr>
                {open === k ? (
                  <tr className="bg-white/[0.02]">
                    <td colSpan={9} className="pb-2">
                      <OrdersTable orders={mine} />
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function HistoryTab({ d }: { d: PortfolioPanel }) {
  const trades = d.trades?.real ?? [];
  const net = trades.reduce((a, t) => a + (t.pnl ?? 0), 0);
  const wins = trades.filter((t) => (t.pnl ?? 0) > 0).length;
  return (
    <Card>
      <CardHeader
        title="سجل الصفقات"
        subtitle={`آخر ${HISTORY_DAYS} يومًا · ${trades.length} صفقة · رابحة ${wins} · الصافي بعد الرسوم ${formatMoney(net)} — اضغط على أي صفقة لتفاصيلها وتعزيزاتها`}
      />
      <TradesTable trades={trades} orders={d.orders?.real ?? []} />
    </Card>
  );
}

function OutageTab({ q, books }: { q: ReturnType<typeof useOutageSim>; books: Record<string, Book> }) {
  const o = q.data;
  if (q.isLoading) return <LoadingState />;
  if (q.isError) return <ErrorState message={(q.error as Error | null)?.message ?? "—"} />;
  if (!o?.enabled) {
    return (
      <Card>
        <EmptyState icon={PauseCircle} title="البوت على السيرفر أقدم من هذه الصفحة" description="حدّث البوت على السيرفر حتى تظهر محاكاة فترة التوقف." />
      </Card>
    );
  }
  if (!o.outage || !o.summary) {
    return (
      <Card>
        <EmptyState icon={PauseCircle} title="لم يتوقف البوت منذ بدء المحاكي" />
      </Card>
    );
  }
  const positions = (o.positions ?? []).map((p) => livePos(p, books));
  const unrealized = positions.reduce((a, p) => a + p.upnl, 0);
  const equity = o.summary.cash + unrealized;
  const ret = (equity / o.summary.start_equity - 1) * 100;
  return (
    <>
      <Card>
        <CardHeader
          title="لو البوت ما توقف"
          subtitle={`توقف ${formatDateTime(o.outage.start)} ← رجع ${formatDateTime(o.outage.end)} (${formatNumber(o.outage.days, 1)} يوم). هنا كل صفقات الثلاثة من لحظة التوقف لحد الآن، محاكاة بنفس القواعد على ${formatUsd(o.capital)} وكأن البوت ما توقف.`}
        />
        <div className="grid grid-cols-2 gap-3 p-4 sm:grid-cols-4">
          <StatCard label="الرصيد الآن" value={formatUsd(equity)} tone="gold" sub={<span className={pnlClass(ret)}>{signedPct(ret)}</span>} />
          <StatCard label="الصافي" value={formatMoney(equity - o.summary.start_equity)} tone={pnlTone(equity - o.summary.start_equity)} sub={`رسوم ${formatUsd(o.summary.fees_paid)}`} />
          <StatCard label="صفقات مغلقة" value={o.summary.trades} sub={o.summary.win_rate === null ? "—" : `رابحة ${formatNumber(o.summary.win_rate * 100, 0)}%`} />
          <StatCard label="مفتوحة الآن" value={positions.length} tone={pnlTone(unrealized)} sub={`غير محقق ${formatMoney(unrealized)}`} />
        </div>
        <p className="border-t border-border-soft px-4 py-2.5 text-xs leading-relaxed text-muted-dim">
          {o.fills} تنفيذًا للمتداولين، بسعرهم نفسه + رسوم الواقعي. الأحجام محسوبة على أرصدة المحافظ الحالية، والتصفية بين
          التنفيذات غير محسوبة. الصفقات اللي كانت مفتوحة قبل التوقف تُترك حتى تُغلق، مثل التشغيل الحي. محاكاة فقط.
        </p>
      </Card>
      {positions.length ? (
        <Card>
          <CardHeader title="مفتوحة الآن بهذه المحاكاة" />
          <div className="grid grid-cols-1 gap-3 p-4 md:grid-cols-2">
            {positions.map((p) => (
              <div key={keyOf(p)} className="glass flex items-center justify-between gap-3 rounded-xl px-4 py-3">
                <span className="flex items-center gap-2">
                  <span className="font-data font-semibold text-bone">{p.coin}</span>
                  <Side side={p.side} />
                  <Wallet wallet={p.wallet} />
                </span>
                <span className="text-end font-data text-sm">
                  <span className="block text-bone">{formatUsd(p.notional)}</span>
                  <span className={pnlClass(p.upnl)}>{formatMoney(p.upnl)}</span>
                  {p.live ? <LiveDot /> : null}
                </span>
              </div>
            ))}
          </div>
        </Card>
      ) : null}
      <Card>
        <CardHeader title="صفقات هذه المحاكاة" subtitle="اضغط على أي صفقة لتفاصيلها وتعزيزاتها" />
        <TradesTable trades={o.trades ?? []} orders={o.orders ?? []} />
      </Card>
    </>
  );
}

type Tab = "open" | "history" | "outage";

export function TrioView() {
  const [capital, setCapital] = useState<number | undefined>(undefined);
  const [leverage, setLeverage] = useState<number | undefined>(undefined);
  const [tab, setTab] = useState<Tab>("open");
  const query = usePortfolio(HISTORY_DAYS, capital, GROUP, leverage);
  const outage = useOutageSim(capital, GROUP, leverage);
  const d = query.data;
  const real = d?.ledger?.real;
  const coins = useMemo(
    () => [...new Set([...(real?.positions ?? []), ...(outage.data?.positions ?? [])].map((p) => p.coin))],
    [real?.positions, outage.data?.positions],
  );
  const books = useLivePrices(coins);
  const positions = (real?.positions ?? []).map((p) => livePos(p, books));
  const unrealized = positions.reduce((a, p) => a + p.upnl, 0);
  const equity = real ? real.cash + unrealized : 0;
  const ret = real ? (equity / real.start_equity - 1) * 100 : 0;
  const cap = d?.capital ?? 10_000;
  const o = outage.data;
  const oNet = o?.summary ? o.summary.cash + (o.positions ?? []).map((p) => livePos(p, books)).reduce((a, p) => a + p.upnl, 0) - o.summary.start_equity : null;

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-bone">محاكي الثلاثي</h1>
          <p className="mt-1 text-sm text-muted">
            <bdi dir="ltr">8bae + 8fc7 + 69b0</bdi> على Hyperliquid — كل صفقة لهم تُنسخ على رأس مالك الوهمي، بدون أي أمر حقيقي.
          </p>
        </div>
        <div className="flex gap-3">
          <div>
            <Label>رأس مالك</Label>
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
        </div>
      </div>

      {query.isLoading ? (
        <LoadingState />
      ) : query.isError || !d ? (
        <ErrorState message={(query.error as Error | null)?.message ?? "—"} />
      ) : !d.enabled || !real ? (
        <Card>
          <EmptyState icon={FlaskConical} title="المحاكي لم يبدأ بعد" description="يبدأ تلقائيًا مع البوت على السيرفر." />
        </Card>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatCard label="رصيدك الآن" value={formatUsd(equity)} tone="gold" sub={`بدأ بـ ${formatUsd(real.start_equity)}`} />
            <StatCard label="الربح الكلي" value={formatMoney(equity - real.start_equity)} tone={pnlTone(equity - real.start_equity)} sub={<span className={pnlClass(ret)}>{signedPct(ret)}</span>} />
            <StatCard label="غير المحقق الآن" value={formatMoney(unrealized)} tone={pnlTone(unrealized)} sub={`${positions.length} صفقة مفتوحة`} />
            <StatCard
              label="صفقات مغلقة"
              value={real.trades}
              sub={real.win_rate === null ? "لا صفقات مغلقة بعد" : `رابحة ${formatNumber(real.win_rate * 100, 0)}%`}
            />
          </div>

          {o?.outage && oNet !== null ? (
            <button
              type="button"
              onClick={() => setTab("outage")}
              className="glass glass-strong flex flex-wrap items-center justify-between gap-3 rounded-2xl px-4 py-3 text-start"
            >
              <span className="flex items-center gap-2 text-sm text-bone">
                <PauseCircle className="h-4 w-4 text-warn" strokeWidth={1.75} />
                لو البوت ما توقف ({formatNumber(o.outage.days, 0)} يوم من {formatDateTime(o.outage.start).split(",")[0]}):
              </span>
              <span className="font-data text-sm">
                {o.summary?.trades ?? 0} صفقة مغلقة ·{" "}
                <bdi dir="ltr" className={pnlClass(oNet)}>
                  {formatMoney(oNet)} ({signedPct((oNet / (o.summary?.start_equity || 1)) * 100)})
                </bdi>
                <span className="ms-3 text-xs text-muted">التفاصيل ←</span>
              </span>
            </button>
          ) : null}

          <div className="glass flex w-fit gap-1 rounded-2xl p-1">
            {(
              [
                ["open", `المفتوحة (${positions.length})`],
                ["history", "السجل"],
                ["outage", "فترة التوقف"],
              ] as [Tab, string][]
            ).map(([k, label]) => (
              <button
                key={k}
                type="button"
                onClick={() => setTab(k)}
                className={cn(
                  "rounded-xl px-4 py-1.5 text-sm transition-colors",
                  tab === k ? "bg-gold/20 text-gold-bright shadow-[inset_0_1px_0_rgba(255,255,255,0.1)]" : "text-muted hover:text-bone",
                )}
              >
                {label}
              </button>
            ))}
          </div>

          {tab === "open" ? <OpenTab d={d} books={books} capital={cap} /> : tab === "history" ? <HistoryTab d={d} /> : <OutageTab q={outage} books={books} />}
        </>
      )}
    </div>
  );
}
