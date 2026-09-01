"use client";

import { useState } from "react";
import { AlertTriangle, Check } from "lucide-react";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { LoadingState } from "@/components/ui/empty-state";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { usePipPreview, useSettings, useUpdateSettings } from "@/hooks/use-settings";
import { formatPrice } from "@/lib/format";
import type { TradingMode, TradingSettingsUpdate } from "@/types/api";

const LIVE_PHRASE = "ENABLE LIVE TRADING";

function Row({
  label,
  description,
  children,
}: {
  label: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 border-b border-border-soft py-4 last:border-0 sm:flex-row sm:items-center sm:justify-between">
      <div className="sm:max-w-sm">
        <p className="text-sm font-medium text-bone">{label}</p>
        <p className="mt-0.5 text-xs text-muted">{description}</p>
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

function PipPreviewPanel({ targetPips }: { targetPips: string }) {
  const debounced = useDebouncedValue(targetPips, 400);
  const previewQuery = usePipPreview(debounced || "0");
  const preview = previewQuery.data;

  if (!targetPips || Number(targetPips) <= 0) {
    return null;
  }

  return (
    <div className="rounded border border-border-soft bg-ink/40 px-3 py-2.5 text-xs">
      <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-muted">
        معاينة النقاط — نفس حساب محرك التداول، غير محسوبة هنا
      </p>
      {previewQuery.isLoading ? (
        <p className="text-muted-dim">جارٍ الحساب…</p>
      ) : !preview || !preview.broker_healthy || preview.pip_size === null ? (
        <p className="text-muted-dim">الوسيط غير متاح — المعاينة تحتاج وسيط متصل.</p>
      ) : (
        <dl className="font-data grid grid-cols-2 gap-x-6 gap-y-1 sm:grid-cols-3">
          <div className="flex justify-between gap-2 sm:block">
            <dt className="text-muted-dim">الهدف</dt>
            <dd className="text-bone">{formatPrice(preview.target_pips)} نقطة</dd>
          </div>
          <div className="flex justify-between gap-2 sm:block">
            <dt className="text-muted-dim">نقطة الرمز</dt>
            <dd className="text-bone">{preview.point}</dd>
          </div>
          <div className="flex justify-between gap-2 sm:block">
            <dt className="text-muted-dim">حجم النقطة</dt>
            <dd className="text-bone">{preview.pip_size}</dd>
          </div>
          <div className="flex justify-between gap-2 sm:block">
            <dt className="text-muted-dim">مسافة السعر</dt>
            <dd className="text-gold-bright">{preview.price_distance}</dd>
          </div>
          {preview.example_entry && (
            <div className="flex justify-between gap-2 sm:block">
              <dt className="text-muted-dim">مثال دخول</dt>
              <dd className="text-bone">{formatPrice(preview.example_entry)}</dd>
            </div>
          )}
          {preview.calculated_take_profit && (
            <div className="flex justify-between gap-2 sm:block">
              <dt className="text-muted-dim">هدف الربح المحسوب</dt>
              <dd className="text-win-bright">{formatPrice(preview.calculated_take_profit)}</dd>
            </div>
          )}
        </dl>
      )}
    </div>
  );
}

export default function SettingsPage() {
  const settingsQuery = useSettings();
  const updateSettings = useUpdateSettings();

  const [form, setForm] = useState<TradingSettingsUpdate | null>(null);
  const [syncedId, setSyncedId] = useState<number | null>(null);
  const [confirmPhrase, setConfirmPhrase] = useState("");
  const [savedFlash, setSavedFlash] = useState(false);

  // Sync the editable form from the fetched settings once (and again if the
  // record itself changes underneath us) — done during render, per React's
  // guidance for adjusting state from props, rather than in a useEffect.
  if (settingsQuery.data && settingsQuery.data.id !== syncedId) {
    setSyncedId(settingsQuery.data.id);
    const rest: Partial<typeof settingsQuery.data> = { ...settingsQuery.data };
    delete rest.id;
    setForm(rest);
  }

  if (settingsQuery.isLoading || !form) {
    return <LoadingState />;
  }

  const original = settingsQuery.data!;
  const switchingToLive = form.mode === "LIVE" && original.mode !== "LIVE";
  const canSave = !switchingToLive || confirmPhrase === LIVE_PHRASE;

  function set<K extends keyof TradingSettingsUpdate>(key: K, value: TradingSettingsUpdate[K]) {
    setForm((prev) => (prev ? { ...prev, [key]: value } : prev));
    setSavedFlash(false);
  }

  function handleSave() {
    if (!form) return;
    const payload: TradingSettingsUpdate = { ...form };
    if (switchingToLive) payload.confirm_live_phrase = confirmPhrase;
    updateSettings.mutate(payload, {
      onSuccess: () => {
        setConfirmPhrase("");
        setSavedFlash(true);
        setTimeout(() => setSavedFlash(false), 2500);
      },
    });
  }

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-bone">إعدادات التداول</h1>
        <p className="mt-1 text-sm text-muted">
          كل تغيير هنا يُسجَّل في سجل التدقيق. هذي هي الإعدادات الوحيدة اللي يلتزم بها البوت —
          أهداف الربح المرسلة من تيليكرام لا تُستخدم أبدًا للتنفيذ.
        </p>
      </div>

      <Card>
        <CardHeader title="التنفيذ" subtitle="ما المسموح للبوت فعله الآن" />
        <CardBody className="p-0 px-4">
          <Row label="التداول التلقائي" description="المفتاح الرئيسي. إيقافه يعني عدم فتح أي صفقة مهما كانت باقي الإعدادات.">
            <Switch checked={!!form.auto_trading} onChange={(v) => set("auto_trading", v)} label="التداول التلقائي" />
          </Row>
          <Row label="إشارات الشراء" description="السماح للبوت بالتصرف بناءً على إشارات BUY GOLD.">
            <Switch checked={!!form.buy_enabled} onChange={(v) => set("buy_enabled", v)} label="الشراء مفعّل" />
          </Row>
          <Row label="إشارات البيع" description="السماح للبوت بالتصرف بناءً على إشارات SELL GOLD. مستقل تمامًا عن مفتاح الشراء أعلاه.">
            <Switch checked={!!form.sell_enabled} onChange={(v) => set("sell_enabled", v)} label="البيع مفعّل" />
          </Row>
          <Row label="إلزامية وقف الخسارة" description="رفض أي إشارة بدون وقف خسارة من القناة بدل التداول بدون حماية.">
            <Switch
              checked={!!form.require_stop_loss}
              onChange={(v) => set("require_stop_loss", v)}
              label="إلزامية وقف الخسارة"
            />
          </Row>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="المخاطرة" subtitle="يُطبَّق على كل صفقة، بغض النظر عمّا تقترحه تيليكرام" />
        <CardBody className="p-0 px-4">
          <Row label="حجم اللوت" description="حجم ثابت لكل أمر شراء.">
            <Input
              type="number"
              step="0.01"
              min="0"
              className="w-28 text-right font-data"
              value={form.lot_size ?? ""}
              onChange={(e) => set("lot_size", e.target.value)}
            />
          </Row>
          <Row label="هدف الربح (نقاط)" description="هدفنا الخاص — رسائل TP1/TP2/TP3 من تيليكرام تُخزَّن لكن تُتجاهل.">
            <Input
              type="number"
              step="1"
              min="0"
              className="w-28 text-right font-data"
              value={form.target_pips ?? ""}
              onChange={(e) => set("target_pips", e.target.value)}
            />
          </Row>
          <div className="py-3">
            <PipPreviewPanel targetPips={form.target_pips ?? ""} />
          </div>
          <Row
            label="أقصى مسافة لستوب القناة (نقاط)"
            description="رفض أي إشارة يكون ستوب القناة فيها أوسع من هذا الحد عن السعر الحالي — فلتر جودة، مو حد مخاطرة (البوت يستخدم ستوب القناة الحرفي دائمًا). أفرغ الحقل لتعطيله بالكامل. القيمة الافتراضية 200 نقطة مبنية على تحليل تاريخي (راجع VIP_FOREX_ANALYSIS.md)."
          >
            <Input
              type="number"
              step="1"
              min="0"
              placeholder="بدون حد"
              className="w-28 text-right font-data"
              value={form.max_channel_stop_pips ?? ""}
              onChange={(e) => set("max_channel_stop_pips", e.target.value === "" ? null : e.target.value)}
            />
          </Row>
          <Row
            label="وقف خسارة تلقائي عند انتهاء المهلة (نقاط)"
            description="لو القناة ما أرسلت وقف خسارة قبل انتهاء مهلة اكتمال الإشارة أعلاه، يضع البوت هذا الوقف تلقائيًا (بالمسافة عن السعر الحالي وقتها) وينفّذ الصفقة بدل ما تنتهي صلاحيتها بدون تنفيذ. أفرغ الحقل للرجوع للسلوك القديم (انتهاء الصلاحية بدون تنفيذ)."
          >
            <Input
              type="number"
              step="1"
              min="0"
              placeholder="بدون تفعيل"
              className="w-28 text-right font-data"
              value={form.auto_sl_fallback_pips ?? ""}
              onChange={(e) => set("auto_sl_fallback_pips", e.target.value === "" ? null : e.target.value)}
            />
          </Row>
          <Row label="أقصى عدد صفقات مفتوحة" description="يمنع أي شراء جديد طالما هذا العدد مفتوح فعلاً.">
            <Input
              type="number"
              step="1"
              min="1"
              className="w-28 text-right font-data"
              value={form.max_open_trades ?? ""}
              onChange={(e) => set("max_open_trades", Number(e.target.value))}
            />
          </Row>
          <Row label="مهلة اكتمال الإشارة" description="عدد الثواني للانتظار لوقف الخسارة قبل انتهاء صلاحية الإشارة بدون تنفيذ.">
            <Input
              type="number"
              step="1"
              min="1"
              className="w-28 text-right font-data"
              value={form.signal_completion_timeout_seconds ?? ""}
              onChange={(e) => set("signal_completion_timeout_seconds", Number(e.target.value))}
            />
          </Row>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="خطة اللوت اليومية/الأسبوعية"
          subtitle="صفقة واحدة باليوم، وثانية فقط عند خسارة الأولى — لا صفقة ثالثة إطلاقًا. الاثنين له لوت خاص كبداية الأسبوع. التوقيت بتوقيت بغداد."
        />
        <CardBody className="p-0 px-4">
          <Row
            label="تفعيل الخطة"
            description="عند التعطيل، كل صفقة تستخدم حجم اللوت الثابت أعلاه مباشرة، بدون حد يومي أو مضاعفة."
          >
            <Switch
              checked={!!form.daily_lot_plan_enabled}
              onChange={(v) => set("daily_lot_plan_enabled", v)}
              label="خطة اللوت اليومية"
            />
          </Row>
          <Row
            label="أقصى عدد صفقات باليوم"
            description="حد صارم — لا تُنفَّذ أي صفقة إضافية بعد الوصول لهذا العدد، بغض النظر عن نتيجتها."
          >
            <Input
              type="number"
              step="1"
              min="1"
              className="w-28 text-right font-data"
              value={form.max_trades_per_day ?? ""}
              onChange={(e) => set("max_trades_per_day", Number(e.target.value))}
            />
          </Row>
          <Row
            label="مضاعف اللوت (أيام عادية)"
            description="صفقة اليوم الثانية — فقط لو خسرت الأولى — تستخدم حجم اللوت الثابت مضروبًا بهذا الرقم."
          >
            <Input
              type="number"
              step="0.1"
              min="0"
              className="w-28 text-right font-data"
              value={form.martingale_lot_multiplier ?? ""}
              onChange={(e) => set("martingale_lot_multiplier", e.target.value)}
            />
          </Row>
          <Row
            label="لوت أول صفقة يوم الاثنين"
            description="بداية الأسبوع — أول صفقة يوم الاثنين تحديدًا تستخدم هذا الحجم بدل حجم اللوت الثابت."
          >
            <Input
              type="number"
              step="0.01"
              min="0"
              className="w-28 text-right font-data"
              value={form.monday_first_trade_lot ?? ""}
              onChange={(e) => set("monday_first_trade_lot", e.target.value)}
            />
          </Row>
          <Row
            label="لوت ثاني صفقة يوم الاثنين"
            description="فقط لو خسرت صفقة الاثنين الأولى — حجم ثابت، مو مضاعفة لوت الاثنين الأول."
          >
            <Input
              type="number"
              step="0.01"
              min="0"
              className="w-28 text-right font-data"
              value={form.monday_second_trade_lot ?? ""}
              onChange={(e) => set("monday_second_trade_lot", e.target.value)}
            />
          </Row>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="وضع التداول" subtitle="محاكاة ← تجريبي ← حقيقي" />
        <CardBody className="flex flex-col gap-4 px-4">
          <Row label="الوضع" description="يتحكم بأي وسيط يستقبل الأوامر.">
            <Select value={form.mode} onChange={(e) => set("mode", e.target.value as TradingMode)}>
              <option value="SIMULATION">محاكاة</option>
              <option value="DEMO">تجريبي</option>
              <option value="LIVE">حقيقي</option>
            </Select>
          </Row>

          {switchingToLive && (
            <div className="rounded border border-loss/40 bg-loss/10 p-3">
              <div className="flex items-center gap-2 text-sm font-medium text-loss-bright">
                <AlertTriangle className="h-4 w-4" />
                هذا يُفعّل التداول بأموال حقيقية
              </div>
              <p className="mt-1 text-xs text-muted">
                ستُعطَّل نقاط المحاكاة فورًا. اكتب{" "}
                <span className="font-data text-bone">{LIVE_PHRASE}</span> للتأكيد.
              </p>
              <Input
                className="mt-2 font-data"
                placeholder={LIVE_PHRASE}
                value={confirmPhrase}
                onChange={(e) => setConfirmPhrase(e.target.value)}
              />
            </div>
          )}
        </CardBody>
      </Card>

      {updateSettings.isError && (
        <p className="text-sm text-loss-bright">{(updateSettings.error as Error).message}</p>
      )}

      <div className="flex items-center gap-3">
        <Button variant="primary" onClick={handleSave} disabled={!canSave || updateSettings.isPending}>
          {updateSettings.isPending ? "جارٍ الحفظ…" : "حفظ التغييرات"}
        </Button>
        {savedFlash && (
          <span className="flex items-center gap-1 text-sm text-win-bright">
            <Check className="h-4 w-4" /> تم الحفظ
          </span>
        )}
      </div>
    </div>
  );
}
