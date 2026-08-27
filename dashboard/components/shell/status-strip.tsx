"use client";

import { arTradingMode, cn } from "@/lib/format";
import { useDashboardSummary, useBrokerHealth, useSignalSourceHealth } from "@/hooks/use-dashboard";

function Dot({ tone, pulse }: { tone: "win" | "loss" | "muted" | "gold"; pulse?: boolean }) {
  const color =
    tone === "win"
      ? "bg-win-bright text-win-bright"
      : tone === "loss"
        ? "bg-loss-bright text-loss-bright"
        : tone === "gold"
          ? "bg-gold-bright text-gold-bright"
          : "bg-muted-dim text-muted-dim";
  return <span className={cn("h-1.5 w-1.5 rounded-full", color, pulse && "pulse")} />;
}

function StripItem({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-1.5 whitespace-nowrap">
      <span className="text-[10px] font-medium uppercase tracking-wide text-muted-dim">{label}</span>
      <span className="flex items-center gap-1.5 text-xs text-bone">{children}</span>
    </div>
  );
}

export function StatusStrip() {
  const { data: summaryData } = useDashboardSummary();
  const { data: broker } = useBrokerHealth();
  const { data: source } = useSignalSourceHealth();

  const settings = summaryData?.settings;
  const autoTrading = settings?.auto_trading ?? false;
  const isTelegram = source?.source === "telegram";

  return (
    <div className="flex h-9 items-center gap-5 overflow-x-auto border-b border-border-soft bg-ink/60 px-4 text-xs">
      <StripItem label={isTelegram ? "تيليكرام" : "مصدر الإشارة"}>
        <span title={source?.detail ?? source?.last_error ?? undefined}>
          <Dot tone={source?.connected ? "win" : "loss"} />
        </span>
        {source?.connected ? "متصل" : "غير متصل"}
      </StripItem>

      <StripItem label="المصدر">
        <span className="font-data">
          {isTelegram ? "تيليكرام حقيقي" : source?.source === "mock" ? "تجريبي" : (source?.source ?? "—")}
        </span>
      </StripItem>

      <StripItem label="الوسيط">
        <Dot tone={broker?.healthy ? "win" : "loss"} />
        <span className="font-data">{broker?.broker ?? "—"}</span>
      </StripItem>

      <StripItem label="الوضع">
        <span
          className={cn(
            "font-data font-medium",
            settings?.mode === "LIVE" ? "text-loss-bright" : "text-gold-bright",
          )}
        >
          {settings?.mode ? arTradingMode(settings.mode) : "—"}
        </span>
      </StripItem>

      <StripItem label="التداول التلقائي">
        <Dot tone={autoTrading ? "gold" : "muted"} pulse={autoTrading} />
        {autoTrading ? "يعمل" : "متوقف"}
      </StripItem>

      {summaryData ? (
        <StripItem label="الرصيد">
          <span className="font-data text-gold-bright">
            ${Number(summaryData.summary.balance ?? 0).toLocaleString("en-US", { maximumFractionDigits: 2 })}
          </span>
        </StripItem>
      ) : null}
    </div>
  );
}
