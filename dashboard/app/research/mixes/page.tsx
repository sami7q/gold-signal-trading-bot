"use client";

import { useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, CardHeader } from "@/components/ui/card";
import { StatCard } from "@/components/ui/stat-card";
import { ResearchGate, Th, WalletChip, multClass, multPct, pct, usd0 } from "@/components/research/common";
import { cn, formatNumber } from "@/lib/format";
import type { ResearchData, ResearchMix } from "@/types/api";

const CHART_TEXT = "#8b8f98";
const LINES = ["#c9a227", "#5b8def", "#4cc38a"];

function MixName({ m }: { m: ResearchMix }) {
  return (
    <span className="flex flex-wrap gap-1">
      {m.labels.map((l, i) => <WalletChip key={l} label={l} address={m.wallets[i]} />)}
    </span>
  );
}

function Curves({ mixes }: { mixes: ResearchMix[] }) {
  const n = Math.max(0, ...mixes.map((m) => m.curve_weekly?.length ?? 0));
  if (n < 2) return <p className="px-4 py-6 text-sm text-muted">لا منحنى لهذه الخلطات.</p>;
  const data = Array.from({ length: n }, (_, i) => {
    const row: Record<string, number> = { week: i };
    mixes.forEach((m, j) => {
      const v = m.curve_weekly?.[i];
      if (v !== undefined) row[`m${j}`] = v;
    });
    return row;
  });
  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={data} margin={{ top: 10, right: 20, left: 10, bottom: 0 }}>
        <CartesianGrid stroke="#262b33" vertical={false} />
        <XAxis dataKey="week" stroke={CHART_TEXT} fontSize={11} tickLine={false} tickFormatter={(v) => String(v)} />
        <YAxis stroke={CHART_TEXT} fontSize={11} tickLine={false} width={80} scale="log" domain={["auto", "auto"]} tickFormatter={(v) => usd0(v)} />
        <Tooltip
          contentStyle={{ background: "#1b1f26", border: "1px solid #262b33", borderRadius: 6 }}
          labelFormatter={(v) => `الأسبوع ${v}`}
          formatter={(value, name) => [usd0(value as number), mixes[Number(String(name).slice(1))]?.labels.join(" + ")]}
        />
        {mixes.map((m, j) => (
          <Line key={m.labels.join("+")} type="monotone" dataKey={`m${j}`} stroke={LINES[j % LINES.length]} strokeWidth={j === 0 ? 2 : 1.5} dot={false} isAnimationActive={false} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

function McRow({ title, m }: { title: string; m?: ResearchMix["mc"] }) {
  const a = m?.["edge_x1.0"];
  const b = m?.["edge_x0.5"];
  if (!a) return null;
  return (
    <tr className="border-b border-border-soft/60 font-data">
      <td className="px-3 py-2 font-sans text-bone">{title}</td>
      <td className="px-3 py-2 text-right text-bone">{usd0(a.median)}</td>
      <td className="px-3 py-2 text-right text-muted">{usd0(a.p10)}</td>
      <td className="px-3 py-2 text-right text-muted">{usd0(a.p90)}</td>
      <td className="px-3 py-2 text-right text-loss-bright">{pct(a.p_loss, 1)}</td>
      <td className="px-3 py-2 text-right text-win-bright">{pct(a.p_x2, 0)}</td>
      <td className="px-3 py-2 text-right text-muted">{pct(a.dd_p90, 0)}</td>
      <td className="px-3 py-2 text-right text-muted">{b ? usd0(b.median) : "—"}</td>
      <td className="px-3 py-2 text-right text-muted">{b ? pct(b.p_loss, 1) : "—"}</td>
    </tr>
  );
}

function Body({ d }: { d: ResearchData }) {
  const mixes = d.mixes ?? [];
  const [sel, setSel] = useState(0);
  const best = mixes[0];
  const trio = d.reference?.trio_v5;
  const shown = [mixes[sel], ...(sel !== 0 && best ? [best] : []), ...(trio ? [trio] : [])].filter(Boolean) as ResearchMix[];
  return (
    <>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard
          label="خلطات مُختبرة"
          value={formatNumber(d.database?.mixes_tested, 0)}
          sub={`نجحت في كل اختبارات الخلطة: ${d.mixes_approved ?? d.database?.mixes_approved ?? "—"} · من أفضل ${d.pool?.length ?? 0} محافظ`}
        />
        <StatCard label="أفضل خلطة — واقعي سنة" value={best ? usd0(best.real.final) : "—"} tone="gold" sub={best ? `${multPct(best.real.mult)} · تراجع ${pct(best.real.dd, 1)}` : undefined} />
        <StatCard
          label="أفضل خلطة — النصفان"
          value={best?.h1 ? `${multPct(best.h1.mult)} / ${multPct(best.h2?.mult)}` : "—"}
          sub="كل نصف سنة وحده بدون تعديل"
        />
        <StatCard
          label="الثلاثي الحالي (للمقارنة)"
          value={trio ? usd0(trio.real.final) : "—"}
          sub={trio ? `${multPct(trio.real.mult)} · تراجع ${pct(trio.real.dd, 1)}` : undefined}
        />
      </div>

      <Card>
        <CardHeader
          title="أفضل الخلطات المعتمدة — كل محافظها نجحت في الاختبارات الثمانية والخلطة نجحت في اختباراتها"
          subtitle="حساب واحد بهامش مشترك: كل محفظة يُحسب حجمها على رصيد الحساب كاملًا (طريقة v5)، رافعة 3x، سقف 20%، $10,000، السيناريو الواقعي. اضغط على خلطة لعرض منحناها"
        />
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1100px] text-left text-sm">
            <thead>
              <tr className="border-b border-border-soft text-[11px] text-muted-dim">
                <Th>#</Th>
                <Th>الخلطة</Th>
                <Th end>بعد سنة</Th>
                <Th end>العائد</Th>
                <Th end>أقصى تراجع</Th>
                <Th end>تصفيات</Th>
                <Th end>النصف الأول</Th>
                <Th end>النصف الثاني</Th>
                <Th end>صفقات</Th>
                <Th end>نجاح</Th>
                <Th end>أشهر خاسرة</Th>
              </tr>
            </thead>
            <tbody className="font-data whitespace-nowrap">
              {[...mixes, ...(trio ? [{ ...trio, rank: undefined }] : [])].map((m, i) => (
                <tr
                  key={m.labels.join("+") + i}
                  onClick={() => i < mixes.length && setSel(i)}
                  className={cn("cursor-pointer border-b border-border-soft/60 hover:bg-white/[0.03]", i === sel && "bg-gold/5", i >= mixes.length && "border-t-2 border-t-border")}
                >
                  <td className="px-3 py-2 text-gold-bright">{m.rank ?? "مرجع"}</td>
                  <td className="px-3 py-2 font-sans"><MixName m={m} /></td>
                  <td className="px-3 py-2 text-right text-bone">{usd0(m.real.final)}</td>
                  <td className={cn("px-3 py-2 text-right", multClass(m.real.mult))}>{multPct(m.real.mult)}</td>
                  <td className={cn("px-3 py-2 text-right", m.real.dd > 0.3 ? "text-warn" : "text-muted")}>{pct(m.real.dd, 1)}</td>
                  <td className={cn("px-3 py-2 text-right", m.real.liq ? "text-loss-bright" : "text-muted")}>{m.real.liq}</td>
                  <td className={cn("px-3 py-2 text-right", multClass(m.h1?.mult))}>{multPct(m.h1?.mult)}</td>
                  <td className={cn("px-3 py-2 text-right", multClass(m.h2?.mult))}>{multPct(m.h2?.mult)}</td>
                  <td className="px-3 py-2 text-right text-muted">{m.real.closed ?? "—"}</td>
                  <td className="px-3 py-2 text-right text-muted">{pct(m.real.win)}</td>
                  <td className="px-3 py-2 text-right text-muted">{m.real.losing_months ?? "—"}/{m.real.months ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {d.mix_tests?.length ? (
        <Card>
          <CardHeader title="اختبارات الخلطة" subtitle="كل خلطة في الجدول أعلاه نجحت فيها كلها" />
          <ol className="grid gap-2 px-4 py-3 text-sm sm:grid-cols-3">
            {d.mix_tests.map((t, i) => (
              <li key={t.key} className="flex gap-2 text-bone">
                <span className="font-data text-gold-bright">{i + 1}.</span>
                {t.ar}
              </li>
            ))}
          </ol>
        </Card>
      ) : null}

      <Card>
        <CardHeader title="منحنى الحساب (واقعي، أسبوعيًا، مقياس لوغاريتمي)" subtitle={shown.map((m) => m.labels.join(" + ")).join("   ·   ")} />
        <div className="h-80 p-4 pl-0" dir="ltr"><Curves mixes={shown} /></div>
      </Card>

      <Card>
        <CardHeader
          title="مونت كارلو — 3,000 سنة عشوائية من أداء الخلطة"
          subtitle="إعادة ترتيب أشهر الأداء الواقعي (كتل 30 يومًا). «نصف الأفضلية» = لو كان ربح المتداولين في المستقبل نصف ما كان"
        />
        <div className="overflow-x-auto">
          <table className="w-full min-w-[960px] text-left text-sm">
            <thead>
              <tr className="border-b border-border-soft text-[11px] text-muted-dim">
                <Th>الخلطة</Th>
                <Th end>الوسيط</Th>
                <Th end>أسوأ 10%</Th>
                <Th end>أفضل 10%</Th>
                <Th end>احتمال الخسارة</Th>
                <Th end>احتمال ×2</Th>
                <Th end>تراجع (90%)</Th>
                <Th end>الوسيط — نصف الأفضلية</Th>
                <Th end>خسارة — نصف الأفضلية</Th>
              </tr>
            </thead>
            <tbody>
              {mixes.filter((m) => m.mc).map((m) => <McRow key={m.labels.join("+")} title={m.labels.join(" + ")} m={m.mc} />)}
              {trio?.mc ? <McRow title={`الثلاثي الحالي (${trio.labels.join(" + ")})`} m={trio.mc} /> : null}
              {d.reference?.loco?.mc ? <McRow title="Loco وحده" m={d.reference.loco.mc} /> : null}
            </tbody>
          </table>
        </div>
        <p className="border-t border-border-soft px-4 py-2.5 text-xs leading-relaxed text-muted-dim">
          الخلطات اختيرت من أداء نفس السنة؛ النصفان ومونت كارلو يخففان ذلك ولا يلغيانه. ليست نصيحة مالية.
        </p>
      </Card>
      {d.mixes_watch?.length ? (
        <Card>
          <CardHeader
            title="خلطات للمراقبة فقط — لا للنسخ"
            subtitle="أرقامها أعلى، لكنها تحتوي محفظة فشلت وحدها في اختبار (غالبًا التراجع فوق 40%). معظم ربحها من تلك المحفظة"
          />
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-left text-sm">
              <thead>
                <tr className="border-b border-border-soft text-[11px] text-muted-dim">
                  <Th>الخلطة</Th>
                  <Th end>بعد سنة</Th>
                  <Th end>أقصى تراجع</Th>
                  <Th end>النصف الأول / الثاني</Th>
                  <Th>المحفظة غير المعتمدة</Th>
                  <Th>نصيبها من الربح</Th>
                </tr>
              </thead>
              <tbody className="font-data whitespace-nowrap">
                {d.mixes_watch.map((m) => {
                  const nl = m.near_wallets ?? [];
                  const total = Object.values(m.by_wallet ?? {}).reduce((a, b) => a + b, 0);
                  const share = nl.reduce((a, l) => a + (m.by_wallet?.[l] ?? 0), 0);
                  return (
                    <tr key={m.labels.join("+")} className="border-b border-border-soft/60">
                      <td className="px-3 py-2 font-sans"><MixName m={m} /></td>
                      <td className="px-3 py-2 text-right text-muted">{usd0(m.real.final)}</td>
                      <td className="px-3 py-2 text-right text-warn">{pct(m.real.dd, 1)}</td>
                      <td className="px-3 py-2 text-right text-muted">{multPct(m.h1?.mult)} / {multPct(m.h2?.mult)}</td>
                      <td className="px-3 py-2 text-loss-bright">{nl.map((l) => `0x${l}`).join("، ")}</td>
                      <td className="px-3 py-2 text-muted">{total > 0 ? pct(share / total, 0) : "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      ) : null}
    </>
  );
}

export default function MixesPage() {
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-bone">البحث — الخليط</h1>
        <p className="mt-1 text-sm text-muted">كل خلطة من 2 إلى 4 محافظ من أفضل المحافظ، منسوخة معًا في حساب واحد لسنة كاملة.</p>
      </div>
      <ResearchGate>{(d) => <Body d={d} />}</ResearchGate>
    </div>
  );
}
