"use client";

import { arMode, brokerLabel, cn, formatRelativeTime, formatUsd } from "@/lib/format";
import { useOverview } from "@/hooks/use-loco";

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
      <span className="text-[10px] font-medium text-muted-dim">{label}</span>
      <span className="flex items-center gap-1.5 text-xs text-bone">{children}</span>
    </div>
  );
}

export function StatusStrip() {
  const { data, isError } = useOverview();

  if (isError || !data) {
    return (
      <div className="flex h-9 items-center gap-2 border-b border-border-soft bg-ink/60 px-4 text-xs text-muted">
        <Dot tone={isError ? "loss" : "muted"} />
        {isError ? "لا اتصال بخادم البوت" : "جارٍ الاتصال…"}
      </div>
    );
  }

  const feedOk = data.bot.running && data.bot.ws_connected;
  const mode = data.mode.mode;

  return (
    <div className="flex h-9 items-center gap-5 overflow-x-auto border-b border-border-soft bg-ink/60 px-4 text-xs">
      <StripItem label="البوت">
        <Dot tone={data.bot.running ? "win" : "loss"} />
        {data.bot.running ? "يعمل" : "متوقف"}
      </StripItem>

      <StripItem label="Loco">
        <span title={`آخر إشارة اتصال ${formatRelativeTime(data.bot.last_frame_at)}`}>
          <Dot tone={feedOk ? "win" : "loss"} pulse={feedOk} />
        </span>
        {feedOk ? "متصل لحظيًا" : "منقطع"}
      </StripItem>

      <StripItem label="الوسيط">
        <span className={cn("font-data", data.mode.live && "text-loss-bright")}>
          {brokerLabel(data.account.broker, data.account.env)}
        </span>
      </StripItem>

      <StripItem label="الوضع">
        <Dot tone={mode === "EXECUTE" ? "gold" : "muted"} pulse={mode === "EXECUTE"} />
        <span className={cn("font-medium", mode === "EXECUTE" ? "text-gold-bright" : "text-bone")}>{arMode(mode)}</span>
      </StripItem>

      {data.risk.kill_switch ? (
        <StripItem label="الإيقاف الطارئ">
          <Dot tone="loss" />
          <span className="font-medium text-loss-bright">مفعّل</span>
        </StripItem>
      ) : null}

      <StripItem label="الرصيد">
        <span className="font-data text-gold-bright">{formatUsd(data.account.equity)}</span>
      </StripItem>
    </div>
  );
}
