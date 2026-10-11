"use client";

import { useState } from "react";
import { AlertTriangle, Check, OctagonX, Play } from "lucide-react";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge, ModeBadge } from "@/components/ui/badge";
import { ErrorState, LoadingState } from "@/components/ui/empty-state";
import { useKill, useOverview, useResume, useSetMode, useSettingsView } from "@/hooks/use-loco";
import { ApiError } from "@/lib/api";
import { brokerLabel, cn, formatNumber } from "@/lib/format";
import type { Mode } from "@/types/api";

const EXECUTE_CONFIRM = "ENABLE EXECUTION";

const MODES: Array<{ mode: Mode; title: string; body: string }> = [
  { mode: "WATCH", title: "مراقبة فقط", body: "يتابع Loco ويسجّل كل حركة، بدون أي حساب أو صفقة." },
  { mode: "OBSERVE", title: "محاكاة بدون تنفيذ", body: "يمر بكل القواعد ويحسب الحجم والهدف كأنه سينفّذ، ويسجّلها كصفقات افتراضية — لا يُرسل شيء للوسيط." },
  { mode: "EXECUTE", title: "تنفيذ فعلي", body: "يفتح ويغلق الصفقات فعليًا على حساب الوسيط لحظة ما يتحرك Loco." },
];

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

function ModeCard({ current, broker, env }: { current: Mode; broker: string; env: string }) {
  const setMode = useSetMode();
  const [picked, setPicked] = useState<Mode>(current);
  const [confirm, setConfirm] = useState("");
  const needsConfirm = picked === "EXECUTE" && current !== "EXECUTE";
  const canApply = picked !== current && (!needsConfirm || confirm.trim() === EXECUTE_CONFIRM);

  return (
    <Card>
      <CardHeader
        title="وضع التشغيل"
        subtitle={`الوسيط: ${brokerLabel(broker, env)} — تغيير الوسيط أو التحويل للحقيقي يتم من السيرفر فقط، وليس من هنا.`}
        action={<ModeBadge mode={current} />}
      />
      <CardBody className="flex flex-col gap-3">
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          {MODES.map((m) => (
            <button
              key={m.mode}
              type="button"
              onClick={() => { setPicked(m.mode); setConfirm(""); }}
              className={cn(
                "rounded-md border p-3 text-right transition-colors",
                picked === m.mode ? "border-gold-dim bg-gold/10" : "border-border hover:border-gold-dim/60",
              )}
            >
              <span className="flex items-center justify-between gap-2">
                <span className={cn("text-sm font-semibold", picked === m.mode ? "text-gold-bright" : "text-bone")}>{m.title}</span>
                {current === m.mode ? <Check className="h-4 w-4 text-win-bright" strokeWidth={2} /> : null}
              </span>
              <span className="mt-1 block text-xs leading-relaxed text-muted">{m.body}</span>
            </button>
          ))}
        </div>

        {needsConfirm ? (
          <div className="rounded border border-loss/40 bg-loss/10 p-3">
            <p className="flex items-center gap-2 text-sm font-medium text-loss-bright">
              <AlertTriangle className="h-4 w-4" />
              {env === "live" ? "هذا يفعّل التداول بأموال حقيقية." : "هذا يرسل أوامر فعلية لحساب الوسيط."}
            </p>
            <p className="mt-1 text-xs text-muted">
              اكتب <span className="font-data text-bone">{EXECUTE_CONFIRM}</span> للتأكيد.
            </p>
            <Input className="mt-2 font-data" placeholder={EXECUTE_CONFIRM} value={confirm} onChange={(e) => setConfirm(e.target.value)} />
          </div>
        ) : null}

        {errorText(setMode.error) ? <p className="text-sm text-loss-bright">{errorText(setMode.error)}</p> : null}

        <div className="flex items-center gap-3">
          <Button
            variant="primary"
            disabled={!canApply || setMode.isPending}
            onClick={() => setMode.mutate({ mode: picked, ...(needsConfirm ? { confirm: confirm.trim() } : {}) }, { onSuccess: () => setConfirm("") })}
          >
            {setMode.isPending ? "جارٍ التطبيق…" : "تطبيق الوضع"}
          </Button>
          <span className="text-xs text-muted-dim">يطبّقه البوت خلال ثوانٍ، بدون إعادة تشغيل.</span>
        </div>
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
        subtitle="يمنع فتح أي صفقة جديدة فورًا. إغلاقات Loco تستمر بالمتابعة دائمًا."
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

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-bone">التحكم والإعدادات</h1>
        <p className="mt-1 text-sm text-muted">كل تغيير هنا يُسجَّل في السجلات باسم المستخدم الذي قام به.</p>
      </div>

      <ModeCard key={s.mode} current={s.mode} broker={s.broker} env={s.broker_env} />
      <EmergencyCard killOn={overview.data?.risk.kill_switch ?? false} />

      <Card>
        <CardHeader title="الاستراتيجية" subtitle="تُعدَّل من ملف .env على السيرفر — للاطلاع هنا" />
        <CardBody className="py-1">
          <Row label="المحفظة المتبوعة" value={<span className="break-all text-xs">{s.leader_address}</span>} />
          <Row label="الهامش لكل صفقة" value={`${formatNumber(Number(st.risk_per_trade) * 100)}% من الرصيد`} />
          <Row label="الرافعة" value={`${st.leverage}x (معزول)`} />
          <Row label="هدف الربح" value={`+${formatNumber(st.take_profit_move_pct, 2)}% من سعر الدخول`} />
          <Row label="التصفية التقريبية" value={`−${formatNumber(st.liquidation_move_pct, 2)}% من سعر الدخول`} />
          <Row label="نسخ تعزيزات Loco" value={st.follow_adds ? "نعم" : "لا (متجاهلة)"} />
          <Row label="متابعة تقليص Loco" value={st.follow_reduces ? "نعم، بنفس النسبة" : "لا"} />
          <Row label="أقصى عدد مراكز" value={st.max_open_positions} />
          <Row label="حد الحجم لكل عملة" value={st.max_notional_per_coin ? `$${formatNumber(st.max_notional_per_coin)}` : "بدون حد"} />
          <Row label="الاتجاهات" value={[st.allow_long && "شراء", st.allow_short && "بيع"].filter(Boolean).join(" + ") || "—"} />
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="حدود الأمان" />
        <CardBody className="py-1">
          <Row label="أقصى ابتعاد عن سعر Loco" value={`${formatNumber(sf.max_entry_drift_pct)}%`} />
          <Row label="أقصى عمر للإشارة" value={`${formatNumber(sf.max_signal_age_ms / 1000)} ث`} />
          <Row label="أقصى انزلاق للأمر" value={`${formatNumber(sf.max_order_slippage_pct)}%`} />
          <Row label="حد الخسارة اليومي" value={`${formatNumber(sf.daily_loss_limit_pct)}%`} />
          <Row label="قاطع تراجع Loco" value={`${formatNumber(sf.leader_dd_breaker_pct)}%`} />
          <Row label="إغلاق تصحيحي بعد" value={`${sf.missed_exit_seconds} ث من إغلاق Loco`} />
        </CardBody>
      </Card>

      <div className="rounded-md border border-warn/40 bg-warn/10 px-4 py-3 text-sm text-warn">
        <p className="flex items-center gap-2 font-medium">
          <AlertTriangle className="h-4 w-4" />
          ملاحظة الاستراتيجية
        </p>
        <p className="mt-1 text-xs leading-relaxed text-muted">
          نتائج البحث المنشورة حسبت الدخول على متوسط سعر Loco بعد تعزيزاته. البوت يتجاهل التعزيزات ويدخل على أول سعر،
          وإعادة الاختبار بهذه الطريقة على بيانات Loco نفسها كانت خاسرة على 180 يومًا. ابدأ بالتجريبي وراقب النتائج قبل
          أي مال حقيقي.
        </p>
      </div>
    </div>
  );
}
