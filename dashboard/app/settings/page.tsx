"use client";

import { useState } from "react";
import Link from "next/link";
import { AlertTriangle, OctagonX, Play } from "lucide-react";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge, ModeBadge } from "@/components/ui/badge";
import { ErrorState, LoadingState } from "@/components/ui/empty-state";
import { useKill, useOverview, useResume, useSettingsView } from "@/hooks/use-loco";
import { ApiError } from "@/lib/api";
import { brokerLabel, formatNumber, leaderName } from "@/lib/format";
import type { SettingsView } from "@/types/api";

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-border-soft py-2.5 last:border-0">
      <span className="text-sm text-muted">{label}</span>
      <span className="font-data text-sm text-bone">{value}</span>
    </div>
  );
}

function errorText(e: unknown): string | null {
  if (!e) return null;
  return e instanceof ApiError ? e.message : "تعذّر تنفيذ الطلب.";
}

function LeadersModesCard({ leaders, broker, env }: { leaders: SettingsView["leaders"]; broker: string; env: string }) {
  return (
    <Card>
      <CardHeader
        title="وضع التشغيل"
        subtitle={`لكل متداول وضعه الخاص — الوسيط: ${brokerLabel(broker, env)}. تغيير الوسيط أو التحويل للحقيقي يتم من السيرفر فقط.`}
        action={
          <Link href="/leaders" className="text-xs text-gold-bright hover:underline">
            إدارة المتداولين ←
          </Link>
        }
      />
      <CardBody className="py-1">
        {leaders.map((l) => (
          <Row
            key={l.label}
            label={leaderName(l.label)}
            value={
              <span className="flex items-center gap-2">
                <ModeBadge mode={l.mode} />
                {l.allow_execute ? null : <Badge>التنفيذ مقفل</Badge>}
              </span>
            }
          />
        ))}
      </CardBody>
    </Card>
  );
}

function EmergencyCard({ killOn }: { killOn: boolean }) {
  const kill = useKill();
  const resume = useResume();
  const [armed, setArmed] = useState(false);
  const error = errorText(kill.error) ?? errorText(resume.error);

  return (
    <Card className={killOn ? "border-loss/50" : undefined}>
      <CardHeader
        title="الإيقاف الطارئ"
        subtitle="يمنع فتح أي صفقة جديدة لكل المتداولين فورًا. إغلاقاتهم تستمر بالمتابعة دائمًا."
        action={killOn ? <Badge tone="loss">مفعّل</Badge> : <Badge tone="win">غير مفعّل</Badge>}
      />
      <CardBody className="flex flex-col gap-3">
        <div className="flex flex-wrap gap-2">
          {killOn ? (
            <Button variant="secondary" disabled={resume.isPending} onClick={() => resume.mutate({})}>
              <Play className="h-4 w-4" />
              استئناف
            </Button>
          ) : (
            <Button variant="danger" disabled={kill.isPending} onClick={() => kill.mutate({ close_all: false })}>
              <OctagonX className="h-4 w-4" />
              إيقاف طارئ
            </Button>
          )}
          {!armed ? (
            <Button variant="danger" onClick={() => setArmed(true)}>إيقاف + إغلاق كل الصفقات</Button>
          ) : (
            <>
              <Button
                variant="danger"
                disabled={kill.isPending}
                onClick={() => kill.mutate({ close_all: true }, { onSuccess: () => setArmed(false) })}
              >
                تأكيد: أغلق كل الصفقات الآن
              </Button>
              <Button variant="ghost" onClick={() => setArmed(false)}>إلغاء</Button>
            </>
          )}
        </div>
        {armed ? <p className="text-xs text-warn">سيُغلق البوت كل صفقة مفتوحة بأمر سوق (تقليص فقط)، ويبقى الإيقاف مفعّلًا.</p> : null}
        {error ? <p className="text-sm text-loss-bright">{error}</p> : null}
      </CardBody>
    </Card>
  );
}

export default function SettingsPage() {
  const settings = useSettingsView();
  const overview = useOverview();

  if (settings.isLoading || overview.isLoading) return <LoadingState />;
  if (settings.isError || !settings.data) return <ErrorState message={(settings.error as Error | null)?.message ?? "—"} />;

  const s = settings.data;
  const st = s.strategy;
  const sf = s.safety;
  const c20 = s.cap20;

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-bone">التحكم والإعدادات</h1>
        <p className="mt-1 text-sm text-muted">كل تغيير هنا يُسجَّل في السجلات باسم المستخدم الذي قام به.</p>
      </div>

      <LeadersModesCard leaders={s.leaders ?? []} broker={s.broker} env={s.broker_env} />
      <EmergencyCard killOn={overview.data?.risk.kill_switch ?? false} />

      {c20 ? (
        <Card>
          <CardHeader title="الاستراتيجية: سقف 20% بهامش مشترك" subtitle="botup.md v4/v5 — من ملف .env على السيرفر، للعرض فقط" />
          <CardBody className="py-1">
            <Row label="حجمنا" value={`حجم المتداول × R، و R = ${c20.k} × رصيدنا ÷ رصيده (ثابتة طوال الصفقة)`} />
            <Row label="سقف الهامش لكل صفقة" value={`${formatNumber(c20.per_trade_margin_cap_pct, 0)}% من الرصيد = حجم حتى ${formatNumber(c20.cap_x_equity, 1)}× الرصيد`} />
            <Row label="الهامش والرافعة" value={`${c20.margin_mode === "CROSS" ? "مشترك (Cross)" : c20.margin_mode} · ${c20.leverage}x`} />
            <Row label="التعزيزات" value="تُنسخ كلها حتى السقف، ثم تُتجاهل (التقليص يُتابَع دائمًا)" />
            <Row label="الخروج" value="مع المتداول فقط — بدون هدف ربح وبدون وقف خسارة" />
            <Row label="حد الحجم الكلي" value={`${formatNumber(c20.max_total_notional_x_equity, 0)}× الرصيد`} />
            <Row label="انقطاع Hyperliquid" value={`بعد ${c20.feed_outage_block_seconds} ث: لا صفقات أو تعزيزات جديدة`} />
            <Row label="قاطع الحساب" value={c20.account_kill_dd_pct ? `تحت ${formatNumber((1 - Number(c20.account_kill_dd_pct)) * 100, 0)}% من القمة` : "غير مفعّل"} />
            <Row label="حساب مشترك للمتداولين" value={c20.shared_equity ? "نعم — كل متداول يُحسب على رصيد الحساب كاملًا" : "لا"} />
            <Row label="الحساب الافتراضي (المحاكاة)" value={`$${formatNumber(c20.virtual_start_equity, 0)} · رسوم ${formatNumber(Number(c20.virtual_fee_rate) * 100, 3)}% بسعر Bybit الحي`} />
          </CardBody>
        </Card>
      ) : null}

      <Card>
        <CardHeader title={c20 ? "إعدادات الاستراتيجية السابقة (أول صفقة)" : "الاستراتيجية"} subtitle="الإعدادات العامة من ملف .env على السيرفر — ويمكن تخصيصها لكل متداول هناك" />
        <CardBody className="py-1">
                    <Row label="الهامش لكل صفقة" value={`${formatNumber(Number(st.risk_per_trade) * 100)}% من الرصيد`} />
          <Row label="الرافعة" value={`${st.leverage}x (معزول)`} />
          <Row label="هدف الربح" value={st.take_profit_move_pct ? `+${formatNumber(st.take_profit_move_pct, 2)}% من سعر الدخول` : "متوقف"} />
          <Row label="التصفية التقريبية" value={`−${formatNumber(st.liquidation_move_pct, 2)}% من سعر الدخول`} />
          <Row label="نسخ تعزيزات المتداول" value={st.follow_adds ? "نعم" : "لا (متجاهلة)"} />
          <Row label="متابعة تقليص المتداول" value={st.follow_reduces ? "نعم، بنفس النسبة" : "لا"} />
          <Row label="أقصى عدد مراكز" value={st.max_open_positions ?? "متوقف"} />
          <Row label="حد الحجم لكل عملة" value={st.max_notional_per_coin ? `$${formatNumber(st.max_notional_per_coin)}` : "بدون حد"} />
          <Row label="الاتجاهات" value={[st.allow_long && "شراء", st.allow_short && "بيع"].filter(Boolean).join(" + ") || "—"} />
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="حدود الأمان" />
        <CardBody className="py-1">
          <Row label="أقصى ابتعاد عن سعر المتداول" value={sf.max_entry_drift_pct ? `${formatNumber(sf.max_entry_drift_pct)}%` : "متوقف"} />
          <Row label="أقصى عمر للإشارة" value={sf.max_signal_age_ms ? `${formatNumber(sf.max_signal_age_ms / 1000)} ث` : "متوقف"} />
          <Row label="أقصى انزلاق للأمر" value={`${formatNumber(sf.max_order_slippage_pct)}%`} />
          <Row label="حد الخسارة اليومي" value={sf.daily_loss_limit_pct ? `${formatNumber(sf.daily_loss_limit_pct)}%` : "متوقف"} />
          <Row label="قاطع تراجع المتداول" value={sf.leader_dd_breaker_pct ? `${formatNumber(sf.leader_dd_breaker_pct)}%` : "متوقف"} />
          <Row label="إغلاق تصحيحي بعد" value={`${sf.missed_exit_seconds} ث من إغلاق المتداول`} />
        </CardBody>
      </Card>

      <div className="rounded-md border border-warn/40 bg-warn/10 px-4 py-3 text-sm text-warn">
        <p className="flex items-center gap-2 font-medium">
          <AlertTriangle className="h-4 w-4" />
          ملاحظة الاستراتيجية
        </p>
        <p className="mt-1 text-xs leading-relaxed text-muted">
          {c20
            ? "المرحلة الحالية: مراقبة الثلاثي 4 أسابيع على الحساب الافتراضي بدون أي أمر. لا تنفيذ (حتى على التجريبي) قبل نجاح شروط الانتقال في صفحة الثلاثي. "
            : null}
          نتائج البحث المنشورة حسبت الدخول على متوسط سعر المتداول بعد تعزيزاته. البوت يتجاهل التعزيزات ويدخل على أول
          سعر، وإعادة الاختبار بهذه الطريقة كانت خاسرة على سنة لـ Loco وللمتداول 3ea معًا (3ea: −91% تراجع). ابدأ
          بالمراقبة والمحاكاة وراقب النتائج قبل أي تنفيذ أو مال حقيقي.
        </p>
      </div>
    </div>
  );
}
