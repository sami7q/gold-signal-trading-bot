"use client";

import { Fragment, useState } from "react";
import { CheckCircle2, ChevronDown, XCircle } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/card";
import { StatCard } from "@/components/ui/stat-card";
import { ResearchGate, Th, WalletChip, multClass, multPct, pct, usd0 } from "@/components/research/common";
import { cn, formatNumber } from "@/lib/format";
import type { ResearchData, ResearchWallet } from "@/types/api";

function TestDots({ w, tests }: { w: ResearchWallet; tests: { key: string; ar: string }[] }) {
  return (
    <span className="inline-flex gap-0.5">
      {tests.map((t) => {
        const ok = w.tests?.[t.key];
        const Icon = ok ? CheckCircle2 : XCircle;
        return (
          <span key={t.key} title={t.ar}>
            <Icon className={cn("h-3.5 w-3.5", ok ? "text-win-bright" : "text-loss-bright")} strokeWidth={2} />
          </span>
        );
      })}
    </span>
  );
}

function Detail({ w, tests }: { w: ResearchWallet; tests: { key: string; ar: string }[] }) {
  const months = Object.entries(w.monthly_base ?? {});
  const max = Math.max(1, ...months.map(([, v]) => Math.abs(v)));
  const m = w.metrics;
  const c = w.copy;
  return (
    <div className="grid gap-4 bg-white/[0.02] px-4 py-4 lg:grid-cols-3">
      <div>
        <div className="mb-1.5 text-[11px] text-muted">الاختبارات</div>
        <ul className="space-y-1 text-sm">
          {tests.map((t) => (
            <li key={t.key} className="flex items-center gap-2">
              {w.tests?.[t.key] ? <CheckCircle2 className="h-4 w-4 text-win-bright" strokeWidth={1.75} /> : <XCircle className="h-4 w-4 text-loss-bright" strokeWidth={1.75} />}
              <span className="text-bone">{t.ar}</span>
            </li>
          ))}
        </ul>
      </div>
      <div className="text-sm">
        <div className="mb-1.5 text-[11px] text-muted">السيناريوهات ($10,000، 3x، سقف 20%)</div>
        <table className="w-full font-data">
          <tbody>
            {[
              ["أساسي (سعره نفسه)", c.base_mult],
              ["واقعي", c.real_mult],
              ["النصف الأول وحده", c.h1_mult],
              ["النصف الثاني وحده", c.h2_mult],
              ["بدون أفضل 5 صفقات", c.no_top5_mult],
              ["تأخير ساعة ضدنا", c.delay60_mult],
              ["رسوم مضاعفة", c.fee2x_mult],
            ].map(([k, v]) => (
              <tr key={k as string} className="border-b border-border-soft/60">
                <td className="py-1 font-sans text-muted">{k}</td>
                <td className={cn("py-1 text-right", multClass(v as number))}>{multPct(v as number)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-2 text-xs text-muted">
          متداول: نجاح {pct(m.win)} · معامل ربح {formatNumber(m.pf, 2)} · {m.n_crypto} صفقة/سنة · احتفاظ وسيط {formatNumber(m.hold_med_h, 0)} س ·
          أشهر رابحة {m.months_pos}/{m.months} · شراء {pct(m.long_share)} · أكثر عملة {m.top_coin} ({pct(m.top_coin_share)})
        </p>
      </div>
      <div>
        <div className="mb-1.5 text-[11px] text-muted">نسخه شهريًا (أساسي، $)</div>
        <div className="flex h-28 items-end gap-1" dir="ltr">
          {months.map(([k, v]) => (
            <div key={k} className="flex h-full flex-1 flex-col items-center justify-end" title={`${k}: ${usd0(v)}`}>
              <div className="flex w-full flex-1 items-end">
                <div className={cn("w-full rounded-sm", v >= 0 ? "bg-win/70" : "bg-loss/70")} style={{ height: `${Math.max(3, (Math.abs(v) / max) * 100)}%` }} />
              </div>
              <span className="mt-1 text-[9px] text-muted-dim">{k.slice(5)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function Table({ rows, tests, ranked }: { rows: ResearchWallet[]; tests: { key: string; ar: string }[]; ranked: boolean }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[1100px] text-left text-sm">
        <thead>
          <tr className="border-b border-border-soft text-[11px] text-muted-dim">
            {ranked ? <Th>#</Th> : null}
            <Th>المحفظة</Th>
            <Th end>واقعي سنة ($10k)</Th>
            <Th end>العائد</Th>
            <Th end>أقصى تراجع</Th>
            <Th end>H1 / H2</Th>
            <Th end>بدون أفضل 5</Th>
            <Th end>نجاح المتداول</Th>
            <Th end>صفقات/سنة</Th>
            <Th end>رصيده</Th>
            <Th>الاختبارات</Th>
            <Th />
          </tr>
        </thead>
        <tbody className="font-data whitespace-nowrap">
          {rows.map((w) => (
            <Fragment key={w.address}>
              <tr className="cursor-pointer border-b border-border-soft/60 hover:bg-white/[0.03]" onClick={() => setOpen(open === w.address ? null : w.address)}>
                {ranked ? <td className="px-3 py-2 text-gold-bright">{w.rank}</td> : null}
                <td className="px-3 py-2 font-sans"><WalletChip label={w.label} address={w.address} /></td>
                <td className="px-3 py-2 text-right text-bone">{usd0(w.copy.real_final)}</td>
                <td className={cn("px-3 py-2 text-right", multClass(w.copy.real_mult))}>{multPct(w.copy.real_mult)}</td>
                <td className={cn("px-3 py-2 text-right", (w.copy.real_dd ?? 0) > 0.3 ? "text-warn" : "text-muted")}>{pct(w.copy.real_dd, 1)}</td>
                <td className="px-3 py-2 text-right">
                  <span className={multClass(w.copy.h1_mult)}>{multPct(w.copy.h1_mult)}</span>
                  <span className="text-muted-dim"> / </span>
                  <span className={multClass(w.copy.h2_mult)}>{multPct(w.copy.h2_mult)}</span>
                </td>
                <td className={cn("px-3 py-2 text-right", multClass(w.copy.no_top5_mult))}>{multPct(w.copy.no_top5_mult)}</td>
                <td className="px-3 py-2 text-right text-muted">{pct(w.metrics.win)}</td>
                <td className="px-3 py-2 text-right text-muted">{w.metrics.n_crypto ?? "—"}</td>
                <td className="px-3 py-2 text-right text-muted">{usd0(w.metrics.av)}</td>
                <td className="px-3 py-2"><TestDots w={w} tests={tests} /> <span className="text-xs text-muted">{w.passed}/{tests.length}</span></td>
                <td className="px-3 py-2"><ChevronDown className={cn("h-4 w-4 text-muted transition-transform", open === w.address && "rotate-180")} /></td>
              </tr>
              {open === w.address ? (
                <tr className="border-b border-border-soft/60">
                  <td colSpan={ranked ? 12 : 11} className="p-0 font-sans whitespace-normal"><Detail w={w} tests={tests} /></td>
                </tr>
              ) : null}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Body({ d }: { d: ResearchData }) {
  const db = d.database!;
  const tests = d.tests ?? [];
  const top = d.top10 ?? [];
  const best = top[0];
  return (
    <>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="محافظ في قاعدة البيانات" value={formatNumber(db.wallets, 0)} sub={`الهدف ${formatNumber(db.target_wallets, 0)} (الضعف)`} tone="gold" />
        <StatCard label="نُسخت محاكاةً" value={formatNumber(db.simulated, 0)} sub={`قائمة مرشحين ${db.shortlist} + قريبون ${db.near}`} />
        <StatCard label="اختُبرت بقوة (10 سيناريوهات)" value={formatNumber(db.robust_tested, 0)} sub={`نجحت في كل الاختبارات: ${db.passed_all_tests}`} />
        <StatCard
          label="الأفضل"
          value={best ? `0x${best.label}` : "—"}
          tone="win"
          sub={best ? `واقعي ${multPct(best.copy.real_mult)} · تراجع ${pct(best.copy.real_dd, 0)}` : undefined}
        />
      </div>

      <Card>
        <CardHeader
          title="أفضل 10 محافظ — نجحت في كل الاختبارات"
          subtitle="مرتبة حسب: العائد الواقعي × (1 − أقصى تراجع). اضغط على أي محفظة للتفاصيل: الاختبارات، السيناريوهات، والأداء الشهري"
        />
        <Table rows={top} tests={tests} ranked />
      </Card>

      {d.near_misses?.length ? (
        <Card>
          <CardHeader title="قريبون من القائمة" subtitle="فشلوا في اختبار واحد فقط — للمراقبة، لا للنسخ" />
          <Table rows={d.near_misses} tests={tests} ranked={false} />
        </Card>
      ) : null}

      <Card>
        <CardHeader title="الاختبارات الثمانية" subtitle="كل محفظة في القائمة نجحت فيها كلها — على آخر 365 يومًا من سجلها" />
        <ol className="grid gap-2 px-4 py-3 text-sm sm:grid-cols-2">
          {tests.map((t, i) => (
            <li key={t.key} className="flex gap-2 text-bone">
              <span className="font-data text-gold-bright">{i + 1}.</span>
              {t.ar}
            </li>
          ))}
        </ol>
        <p className="border-t border-border-soft px-4 py-2.5 text-xs leading-relaxed text-muted-dim">
          «واقعي» = حذف صفقاته الأقصر من ساعة + كل تنفيذ متأخر 15 دقيقة والسعر ضدنا + رسوم 0.15% لكل جهة + سقف $50,000 لكل صفقة. ليست نصيحة مالية.
        </p>
      </Card>
    </>
  );
}

export default function ResearchPage() {
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-bone">البحث — أفضل 10 محافظ</h1>
        <p className="mt-1 text-sm text-muted">محافظ Hyperliquid من قاعدة بياناتنا، بعد نسخها محاكاةً لسنة كاملة واجتياز الاختبارات الثمانية.</p>
      </div>
      <ResearchGate>{(d) => <Body d={d} />}</ResearchGate>
    </div>
  );
}
