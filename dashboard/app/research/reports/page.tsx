"use client";

import { Card, CardHeader } from "@/components/ui/card";
import { StatCard } from "@/components/ui/stat-card";
import { ResearchGate, multPct, pct, usd0 } from "@/components/research/common";
import { formatNumber } from "@/lib/format";
import type { ResearchData } from "@/types/api";

const FUNNEL_AR: Record<string, string> = {
  "with trades": "لها صفقات في السنة",
  not_capped: "سجل كامل (غير مقطوع)",
  min_trades: "عدد صفقات كافٍ",
  max_trades_per_day: "ليست بوت سكالبينغ",
  min_months_active: "نشطة أشهرًا كافية",
  positive_months_ratio: "أغلب أشهرها رابحة",
  profitable_year: "رابحة في السنة",
  both_halves_positive: "رابحة في النصفين",
  win_rate: "نسبة نجاح كافية",
  profit_factor: "معامل ربح كافٍ",
  single_loss_tail: "لا خسارة واحدة مدمرة",
  not_one_lucky_trade: "ليست صفقة حظ واحدة",
  active_recently: "نشطة مؤخرًا",
  account_size: "حجم حساب كافٍ",
  lifetime_profitable: "رابحة طوال عمرها",
  survived_oct25: "نجت من انهيار أكتوبر 2025",
  crypto_is_the_edge: "ربحها من العملات الرقمية",
  tradable_on_bybit: "عملاتها متاحة للتنفيذ",
};

const STEPS = [
  ["جمع", "سنة كاملة من صفقات كل محفظة من Hyperliquid (userFillsByTime) لكل محافظ لوحة الصدارة"],
  ["فلترة", "18 شرطًا (القمع أدناه) لاستبعاد الحظ والبوتات والمحافظ الميتة"],
  ["محاكاة النسخ", "نسخ كل مرشح على حساب $10,000 برافعة 3x وسقف 20% بنفس طريقة البوت"],
  ["اختبار قوي", "10 سيناريوهات: أساسي، واقعي، تأخير ساعة، رسوم مضاعفة، بدون أفضل 5 صفقات، النصفان..."],
  ["الاختبارات الثمانية", "لا تدخل محفظة قائمة أفضل 10 إلا إذا نجحت في الثمانية كلها"],
  ["الخليط", "كل تركيبة من 1 إلى 4 محافظ من أفضل 10، في حساب واحد مشترك، ثم اختبارات الخلطة الخمسة"],
  ["مونت كارلو", "3,000 سنة عشوائية لأفضل 5 خلطات، بالأفضلية كاملة وبنصفها"],
];

function Body({ d }: { d: ResearchData }) {
  const db = d.database!;
  const funnel = d.funnel ?? [];
  const max = Math.max(1, ...funnel.map(([, n]) => n));
  const best = d.mixes?.[0];
  const trio = d.reference?.trio_v5;
  const loco = d.reference?.loco;
  const progress = Math.min(1, db.wallets / Math.max(1, db.target_wallets));
  return (
    <>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="تاريخ آخر بحث" value={d.generated ?? "—"} sub={d.data_until_ms ? `البيانات حتى ${new Date(d.data_until_ms).toISOString().slice(0, 10)}` : undefined} />
        <StatCard label="قاعدة البيانات" value={formatNumber(db.wallets, 0)} tone="gold" sub={`الهدف ${formatNumber(db.target_wallets, 0)} · ${pct(progress, 0)} منجز`} />
        <StatCard label="محافظ نجحت في كل الاختبارات" value={formatNumber(db.passed_all_tests, 0)} tone="win" sub={`من ${db.robust_tested} اختُبرت بقوة`} />
        <StatCard label="خلطات معتمدة" value={formatNumber(d.mixes_approved ?? db.mixes_approved, 0)} sub={`من ${formatNumber(db.mixes_tested, 0)} خلطة مُختبرة`} />
      </div>

      <Card>
        <CardHeader title="تقدم مضاعفة قاعدة البيانات" subtitle="يزيد بعد كل تشغيل لـ run_research_x2.bat على جهازك ثم research-import على السيرفر" />
        <div className="px-4 py-4">
          <div className="h-3 w-full overflow-hidden rounded bg-white/5">
            <div className="h-full rounded bg-gold/70" style={{ width: `${progress * 100}%` }} />
          </div>
          <div className="mt-2 flex justify-between font-data text-xs text-muted">
            <span>{formatNumber(db.wallets, 0)} محفظة</span>
            <span>{formatNumber(db.target_wallets, 0)}</span>
          </div>
        </div>
      </Card>

      <Card>
        <CardHeader title="الخلاصة" subtitle="السيناريو الواقعي، $10,000، سنة كاملة" />
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-left text-sm">
            <thead>
              <tr className="border-b border-border-soft text-[11px] text-muted-dim">
                <th className="px-3 py-2 font-medium">الاستراتيجية</th>
                <th className="px-3 py-2 text-right font-medium">بعد سنة</th>
                <th className="px-3 py-2 text-right font-medium">العائد</th>
                <th className="px-3 py-2 text-right font-medium">أقصى تراجع</th>
                <th className="px-3 py-2 text-right font-medium">مونت كارلو — الوسيط</th>
                <th className="px-3 py-2 text-right font-medium">احتمال الخسارة</th>
              </tr>
            </thead>
            <tbody className="font-data">
              {[
                best ? ["أفضل خلطة", best] as const : null,
                trio ? ["الثلاثي الحالي", trio] as const : null,
                loco ? ["Loco وحده", loco] as const : null,
              ]
                .filter((x): x is NonNullable<typeof x> => x !== null)
                .map(([name, m]) => (
                  <tr key={name} className="border-b border-border-soft/60">
                    <td className="px-3 py-2 font-sans text-bone">
                      {name}
                      {m.labels.length > 1 ? <bdi dir="ltr" className="ms-2 font-data text-xs text-muted">{m.labels.join(" + ")}</bdi> : null}
                    </td>
                    <td className="px-3 py-2 text-right text-bone">{usd0(m.real.final)}</td>
                    <td className="px-3 py-2 text-right text-win-bright">{multPct(m.real.mult)}</td>
                    <td className="px-3 py-2 text-right text-muted">{pct(m.real.dd, 1)}</td>
                    <td className="px-3 py-2 text-right text-muted">{m.mc ? usd0(m.mc["edge_x1.0"].median) : "—"}</td>
                    <td className="px-3 py-2 text-right text-muted">{m.mc ? pct(m.mc["edge_x1.0"].p_loss, 1) : "—"}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="قمع الفلترة" subtitle="كم محفظة بقيت بعد كل شرط" />
          <ul className="space-y-1.5 px-4 py-3">
            {funnel.map(([k, n]) => (
              <li key={k} className="text-xs">
                <div className="flex justify-between text-muted">
                  <span>{FUNNEL_AR[k] ?? k}</span>
                  <span className="font-data text-bone">{formatNumber(n, 0)}</span>
                </div>
                <div className="mt-0.5 h-1.5 rounded bg-white/5">
                  <div className="h-full rounded bg-gold/60" style={{ width: `${Math.max(1, (n / max) * 100)}%` }} />
                </div>
              </li>
            ))}
          </ul>
        </Card>

        <Card>
          <CardHeader title="المنهجية" subtitle="كيف وصلنا للأرقام" />
          <ol className="space-y-2 px-4 py-3 text-sm">
            {STEPS.map(([t, s], i) => (
              <li key={t} className="flex gap-2">
                <span className="font-data text-gold-bright">{i + 1}.</span>
                <span>
                  <span className="text-bone">{t}: </span>
                  <span className="text-muted">{s}</span>
                </span>
              </li>
            ))}
          </ol>
          {d.settings ? (
            <div className="border-t border-border-soft px-4 py-3 text-xs text-muted">
              رافعة {d.settings.leverage}x · سقف {d.settings.cap_pct}% · رصيد ${formatNumber(Number(d.settings.start), 0)} · واقعي = {String(d.settings.realistic)}
            </div>
          ) : null}
        </Card>
      </div>

      <Card>
        <CardHeader title="تحفظات مهمة" />
        <ul className="list-disc space-y-1.5 px-8 py-3 text-sm text-muted">
          {(d.caveats ?? []).map((c) => <li key={c}>{c}</li>)}
        </ul>
      </Card>

      <Card>
        <CardHeader title="سجل التقارير" subtitle="كل تشغيل يُحفظ على السيرفر باسم تاريخه (data/research)" />
        <ul className="px-4 py-3 font-data text-sm text-muted">
          {(d.history ?? []).length ? (d.history ?? []).slice().reverse().map((h) => <li key={h}>{h}</li>) : <li>—</li>}
        </ul>
      </Card>
    </>
  );
}

export default function ResearchReportsPage() {
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-bone">البحث — التقارير</h1>
        <p className="mt-1 text-sm text-muted">حالة قاعدة البيانات، طريقة البحث، والتحفظات.</p>
      </div>
      <ResearchGate>{(d) => <Body d={d} />}</ResearchGate>
    </div>
  );
}
