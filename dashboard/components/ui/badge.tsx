import { arResult, arSeverity, arTradeStatus, arSignalStatus, cn } from "@/lib/format";

type Tone = "neutral" | "gold" | "win" | "loss" | "warn" | "info";

const TONE_CLASSES: Record<Tone, string> = {
  neutral: "bg-white/5 text-muted border-border",
  gold: "bg-gold/10 text-gold-bright border-gold-dim/60",
  win: "bg-win/10 text-win-bright border-win/40",
  loss: "bg-loss/10 text-loss-bright border-loss/40",
  warn: "bg-warn/10 text-warn border-warn/40",
  info: "bg-info/10 text-info border-info/40",
};

export function Badge({
  tone = "neutral",
  className,
  children,
}: {
  tone?: Tone;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-wide",
        TONE_CLASSES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

const SIGNAL_STATUS_TONE: Record<string, Tone> = {
  RECEIVED: "neutral",
  CLASSIFIED: "neutral",
  WAITING_FOR_DETAILS: "warn",
  READY: "info",
  VALIDATING: "info",
  REJECTED: "loss",
  EXECUTION_PENDING: "info",
  EXECUTED: "win",
  FAILED: "loss",
  EXPIRED: "loss",
  IGNORED: "neutral",
  DUPLICATE: "warn",
};

const TRADE_STATUS_TONE: Record<string, Tone> = {
  CREATED: "neutral",
  SUBMITTING: "info",
  OPEN: "gold",
  CLOSED_TP: "win",
  CLOSED_SL: "loss",
  CLOSED_MANUAL: "neutral",
  REJECTED: "loss",
  FAILED: "loss",
  UNKNOWN: "neutral",
};

const RESULT_TONE: Record<string, Tone> = {
  WIN: "win",
  LOSS: "loss",
  BREAKEVEN: "neutral",
};

const SEVERITY_TONE: Record<string, Tone> = {
  INFO: "neutral",
  WARNING: "warn",
  ERROR: "loss",
  CRITICAL: "loss",
};

export function StatusBadge({ status }: { status: string }) {
  return <Badge tone={SIGNAL_STATUS_TONE[status] ?? "neutral"}>{arSignalStatus(status)}</Badge>;
}

export function TradeStatusBadge({ status }: { status: string }) {
  return <Badge tone={TRADE_STATUS_TONE[status] ?? "neutral"}>{arTradeStatus(status)}</Badge>;
}

export function ResultBadge({ result }: { result: string | null }) {
  if (!result) return <span className="text-muted">—</span>;
  return <Badge tone={RESULT_TONE[result] ?? "neutral"}>{arResult(result)}</Badge>;
}

export function SeverityBadge({ severity }: { severity: string }) {
  return <Badge tone={SEVERITY_TONE[severity] ?? "neutral"}>{arSeverity(severity)}</Badge>;
}
