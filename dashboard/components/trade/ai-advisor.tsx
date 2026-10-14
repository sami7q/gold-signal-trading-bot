"use client";

// botup.md §V8 -- the AI analysis of one open trade: the numbers the bot computes every minute (estimated success
// probability from the trader's own record, risk score and its reasons, market state now and at his entry) and
// Claude's written analysis (likely entry reason, thesis, risks, exit plan, manual-exit hint), plus the exit
// alerts of this trade. Dashboard only (no Telegram) -- the owner's choice.

import { AlertTriangle, Bell, Bot, RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useAdvice, useRefreshAdvice } from "@/hooks/use-loco";
import { cn, formatDateTime, formatNumber, formatPercent, formatPrice, formatRelativeTime } from "@/lib/format";
import type { AdviceData, MarketState } from "@/types/api";

const VERDICT: Record<string, { label: string; tone: "win" | "gold" | "warn" | "loss" | "info" }> = {
  hold: { label: "استمر مع المتداول", tone: "win" },
  watch: { label: "راقب عن قرب", tone: "info" },
  take_partial: { label: "فكّر بجني ربح جزئي", tone: "gold" },
  exit: { label: "فكّر بالخروج", tone: "loss" },
};
const CONFIDENCE: Record<string, string> = { low: "ثقة منخفضة", medium: "ثقة متوسطة", high: "ثقة عالية" };
const SEVERITY: Record<string, { label: string; tone: "info" | "warn" | "loss" }> = {
  info: { label: "معلومة", tone: "info" },
  medium: { label: "انتبه", tone: "warn" },
  high: { label: "عاجل", tone: "loss" },
};
const TREND: Record<string, string> = { up: "صاعد", down: "هابط", range: "عرضي" };
const KIND: Record<string, string> = { open: "عند فتح الصفقة", refresh: "تحديث دوري", alert: "بعد تنبيه عاجل", manual: "بطلبك" };

function pct(v: number | null | undefined, digits = 0): string {
  return v === null || v === undefined ? "—" : formatPercent(v * 100, digits);
}

function Meter({ value, tone, label, sub }: { value: number; tone: "win" | "warn" | "loss"; label: string; sub: string }) {
  const color = tone === "win" ? "bg-win" : tone === "warn" ? "bg-warn" : "bg-loss";
  return (
    <div className="flex flex-col gap-1.5 rounded-md border border-border bg-surface/60 p-3">
      <div className="text-[11px] text-muted">{label}</div>
      <div className="font-data text-2xl text-bone">{formatNumber(value, 0)}%</div>
      <div className="h-1.5 w-full overflow-hidden rounded bg-white/5">
        <div className={cn("h-full", color)} style={{ width: `${Math.max(2, Math.min(100, value))}%` }} />
      </div>
      <div className="text-xs text-muted">{sub}</div>
    </div>
  );
}

function MarketRow({ title, m, side }: { title: string; m: MarketState | null; side: number }) {
  if (!m) return null;
  const favour = (side > 0 && m.trend === "up") || (side < 0 && m.trend === "down");
  return (
    <tr className="border-b border-border-soft/60">
      <td className="px-3 py-1.5 font-sans text-muted">{title}</td>
      <td className={cn("px-3 py-1.5", favour ? "text-win-bright" : m.trend === "range" ? "text-muted" : "text-loss-bright")}>{TREND[m.trend]}</td>
      <td className="px-3 py-1.5 text-right">{formatNumber(m.rsi14, 0)}</td>
      <td className="px-3 py-1.5 text-right">{formatNumber(m.atr14_pct, 2)}%</td>
      <td className="px-3 py-1.5 text-right">{m.change_24h_pct === null ? "—" : `${formatNumber(m.change_24h_pct, 2)}%`}</td>
      <td className="px-3 py-1.5 text-right">{formatNumber(m.from_high_7d_pct, 1)}% / +{formatNumber(m.from_low_7d_pct, 1)}%</td>
      <td className="px-3 py-1.5 text-right">{formatPrice(m.price)}</td>
    </tr>
  );
}

function Body({ d, wallet, coin }: { d: AdviceData; wallet: string; coin: string }) {
  const refresh = useRefreshAdvice();
  const live = d.live;
  const ai = d.analysis?.ai;
  if (!live) {
    return <p className="px-4 py-6 text-sm text-muted">لا تحليل لهذه الصفقة — التحليل للصفقات المفتوحة فقط، ويبدأ خلال دقيقة من فتحها.</p>;
  }
  const st = live.stats;
  const prob = (st?.probability ?? 0) * 100;
  const riskTone = live.risk_level === "low" ? "win" : live.risk_level === "medium" ? "warn" : "loss";
  const verdict = ai?.verdict ? VERDICT[ai.verdict] : null;
  const error = d.last_attempt?.meta?.error;
  return (
    <div className="flex flex-col gap-4 p-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Meter
          value={prob}
          tone={prob >= 60 ? "win" : prob >= 45 ? "warn" : "loss"}
          label="احتمال نجاح الصفقة (تقديري)"
          sub={st ? `من ${st.trades} صفقة مغلقة للمتداول` : "سجل المتداول غير متاح"}
        />
        <Meter value={live.risk_score} tone={riskTone} label="درجة الخطورة" sub={live.risk_level === "low" ? "منخفضة" : live.risk_level === "medium" ? "متوسطة" : "عالية"} />
        <div className="flex flex-col gap-1.5 rounded-md border border-border bg-surface/60 p-3">
          <div className="text-[11px] text-muted">رأي التحليل</div>
          {verdict ? <Badge tone={verdict.tone} className="w-fit text-sm">{verdict.label}</Badge> : <span className="text-sm text-muted-dim">—</span>}
          <div className="text-xs text-muted">{ai?.confidence ? CONFIDENCE[ai.confidence] : ""}</div>
        </div>
        <div className="flex flex-col gap-1.5 rounded-md border border-border bg-surface/60 p-3">
          <div className="text-[11px] text-muted">الصفقة الآن</div>
          <div className={cn("font-data text-2xl", live.ret >= 0 ? "text-win-bright" : "text-loss-bright")}>{`${live.ret >= 0 ? "+" : ""}${formatNumber(live.ret * 100, 2)}%`}</div>
          <div className="text-xs text-muted">مفتوحة منذ {formatNumber(live.age_h, 1)} ساعة · حركة السعر منذ دخولنا (موجب = لصالحنا)</div>
        </div>
      </div>

      {live.risk_reasons.length ? (
        <div className="rounded-md border border-border-soft px-3 py-2 text-sm">
          <div className="mb-1 text-[11px] text-muted">أسباب درجة الخطورة</div>
          <ul className="list-inside list-disc space-y-0.5 text-bone">
            {live.risk_reasons.map((r) => <li key={r}>{r}</li>)}
          </ul>
        </div>
      ) : null}

      <div className="rounded-md border border-gold-dim/40 bg-gold/5 p-3 text-sm leading-relaxed">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <span className="flex items-center gap-1.5 font-medium text-gold-bright"><Bot className="h-4 w-4" strokeWidth={1.75} /> تحليل الذكاء الاصطناعي (Claude)</span>
          <span className="flex items-center gap-2 text-xs text-muted">
            {d.analysis ? `${KIND[d.analysis.kind] ?? d.analysis.kind} · ${formatRelativeTime(d.analysis.time)}${d.analysis.meta?.model ? ` · ${d.analysis.meta.model}` : ""}` : "لا تحليل مكتوب بعد"}
            <Button
              size="sm"
              variant="secondary"
              disabled={!d.ai_enabled || d.pending || refresh.isPending}
              onClick={() => refresh.mutate({ wallet, coin })}
            >
              <RefreshCw className={cn("h-3.5 w-3.5", (d.pending || refresh.isPending) && "animate-spin")} strokeWidth={1.75} />
              {d.pending ? "جارٍ التحليل…" : "حلّل الآن"}
            </Button>
          </span>
        </div>
        {!d.ai_enabled ? (
          <p className="text-warn">التحليل المكتوب متوقف: لا يوجد مفتاح Anthropic على السيرفر. شغّل <code dir="ltr">loco-copy set-key anthropic</code> ثم أعد تشغيل البوت. الأرقام أعلاه تعمل بدونه.</p>
        ) : null}
        {error && (!d.analysis || d.last_attempt?.time !== d.analysis.time) ? <p className="text-warn">آخر محاولة: {error}</p> : null}
        {ai ? (
          <div className="grid gap-3 md:grid-cols-2">
            <div><div className="text-[11px] text-muted">سبب الدخول (استنتاج)</div><p className="text-bone">{ai.entry_reason}</p></div>
            <div><div className="text-[11px] text-muted">الفكرة والوضع الآن</div><p className="text-bone">{ai.thesis}</p></div>
            <div><div className="text-[11px] text-muted">تعليق على احتمال النجاح</div><p className="text-bone">{ai.success_comment}</p></div>
            <div>
              <div className="text-[11px] text-muted">أين الخطورة</div>
              <ul className="list-inside list-disc text-bone">{(ai.risks ?? []).map((r) => <li key={r}>{r}</li>)}</ul>
            </div>
            <div><div className="text-[11px] text-muted">خطة الخروج</div><p className="text-bone">{ai.exit_plan}</p></div>
            <div><div className="text-[11px] text-muted">متى تخرج يدويًا</div><p className="text-bone">{ai.manual_exit_hint}</p></div>
          </div>
        ) : null}
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="overflow-x-auto rounded-md border border-border-soft">
          <div className="border-b border-border-soft px-3 py-2 text-[11px] text-muted">سجل المتداول ({wallet})</div>
          {st ? (
            <table className="w-full text-sm">
              <tbody className="font-data">
                <tr className="border-b border-border-soft/60"><td className="px-3 py-1.5 font-sans text-muted">نسبة ربحه الكلية</td><td className="px-3 py-1.5 text-right">{pct(st.win_rate)} من {st.trades}</td></tr>
                <tr className="border-b border-border-soft/60"><td className="px-3 py-1.5 font-sans text-muted">على نفس الاتجاه</td><td className="px-3 py-1.5 text-right">{pct(st.side_win_rate)} من {st.side_trades}</td></tr>
                <tr className="border-b border-border-soft/60"><td className="px-3 py-1.5 font-sans text-muted">على {coin} بنفس الاتجاه</td><td className="px-3 py-1.5 text-right">{pct(st.coin_win_rate)} من {st.coin_trades}</td></tr>
                <tr className="border-b border-border-soft/60"><td className="px-3 py-1.5 font-sans text-muted">صفقاته التي استمرت مثل هذه</td><td className="px-3 py-1.5 text-right">{pct(st.survived_win_rate)} من {st.survived_trades}</td></tr>
                <tr className="border-b border-border-soft/60"><td className="px-3 py-1.5 font-sans text-muted">مدة صفقته (وسيط / 75% / 90%)</td><td className="px-3 py-1.5 text-right">{formatNumber(st.hold_p50_h, 0)} / {formatNumber(st.hold_p75_h, 0)} / {formatNumber(st.hold_p90_h, 0)} س</td></tr>
                <tr className="border-b border-border-soft/60"><td className="px-3 py-1.5 font-sans text-muted">ربحه المعتاد (وسيط / 75%)</td><td className="px-3 py-1.5 text-right text-win-bright">{pct(st.win_ret_p50, 2)} / {pct(st.win_ret_p75, 2)}</td></tr>
                <tr><td className="px-3 py-1.5 font-sans text-muted">خسارته المعتادة (وسيط / 75%)</td><td className="px-3 py-1.5 text-right text-loss-bright">{pct(st.loss_ret_p50, 2)} / {pct(st.loss_ret_p75, 2)}</td></tr>
              </tbody>
            </table>
          ) : <p className="px-3 py-3 text-sm text-muted">غير متاح</p>}
          {st ? <p className="border-t border-border-soft px-3 py-1.5 text-[11px] leading-relaxed text-muted-dim">{st.method}. الربح والخسارة كنسبة من أكبر حجم للصفقة.</p> : null}
        </div>
        <div className="overflow-x-auto rounded-md border border-border-soft">
          <div className="border-b border-border-soft px-3 py-2 text-[11px] text-muted">السوق (فريم الساعة)</div>
          <table className="w-full min-w-[520px] text-left text-sm">
            <thead><tr className="border-b border-border-soft text-[11px] text-muted-dim">
              <th className="px-3 py-1.5 font-medium" /><th className="px-3 py-1.5 font-medium">الاتجاه</th><th className="px-3 py-1.5 text-right font-medium">RSI</th>
              <th className="px-3 py-1.5 text-right font-medium">ATR</th><th className="px-3 py-1.5 text-right font-medium">24س</th>
              <th className="px-3 py-1.5 text-right font-medium">من قمة/قاع 7 أيام</th><th className="px-3 py-1.5 text-right font-medium">السعر</th>
            </tr></thead>
            <tbody className="font-data whitespace-nowrap">
              <MarketRow title="وقت دخوله" m={live.market_at_entry} side={live.side} />
              <MarketRow title="الآن" m={live.market} side={live.side} />
            </tbody>
          </table>
          <p className="border-t border-border-soft px-3 py-1.5 text-[11px] text-muted-dim">
            التمويل {live.ctx?.funding ? `${formatNumber(Number(live.ctx.funding) * 24 * 365 * 100, 1)}% سنويًا` : "—"} · البعد عن تصفية الحساب{" "}
            {live.liq_distance ? `${formatNumber(live.liq_distance, 1)}×` : "—"}
          </p>
        </div>
      </div>

      <div className="rounded-md border border-border-soft">
        <div className="flex items-center gap-1.5 border-b border-border-soft px-3 py-2 text-[11px] text-muted"><Bell className="h-3.5 w-3.5" strokeWidth={1.75} /> تنبيهات الخروج لهذه الصفقة (في اللوحة فقط)</div>
        {d.alerts.length ? (
          <ul className="divide-y divide-border-soft/60">
            {d.alerts.map((a) => (
              <li key={a.id} className="flex flex-wrap items-center gap-3 px-3 py-2 text-sm">
                <Badge tone={SEVERITY[a.severity]?.tone ?? "info"}>{SEVERITY[a.severity]?.label ?? a.severity}</Badge>
                <span className="flex-1 text-bone">{a.text}</span>
                <span className="font-data text-xs text-muted">{formatDateTime(a.time)}{a.price ? ` · ${formatPrice(a.price)}` : ""}</span>
              </li>
            ))}
          </ul>
        ) : <p className="px-3 py-3 text-sm text-muted">لا تنبيهات حتى الآن.</p>}
      </div>

      <p className="flex items-start gap-1.5 text-[11px] leading-relaxed text-muted-dim">
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.75} />
        الاستراتيجية المختبرة تخرج مع المتداول فقط — أي خروج يدوي يخرج عنها ولا تشمله نتائج الاختبار. الاحتمال تقدير إحصائي من سجل المتداول،
        و«سبب الدخول» استنتاج (المتداول لا يعلن أسبابه). التحليل {d.calls_today}/{d.max_calls} اليوم. ليست نصيحة مالية.
      </p>
    </div>
  );
}

export function AiAdvisor({ wallet, coin }: { wallet: string; coin: string }) {
  const q = useAdvice(wallet, coin);
  if (q.isLoading) return <p className="px-4 py-6 text-sm text-muted">تحميل التحليل…</p>;
  if (q.isError || !q.data) return <p className="px-4 py-6 text-sm text-warn">تعذّر تحميل التحليل.</p>;
  if (!q.data.enabled) {
    return <p className="px-4 py-6 text-sm text-muted">محلل الصفقات لم يبدأ بعد على السيرفر (يحتاج تحديث البوت وإعادة تشغيله).</p>;
  }
  return <Body d={q.data} wallet={wallet} coin={coin} />;
}
