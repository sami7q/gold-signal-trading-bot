import { arCopyStatus, arDecision, arEventType, arMode, arSeverity, arSide, cn, leaderName } from "@/lib/format";

type Tone = "neutral" | "gold" | "win" | "loss" | "warn" | "info";

const TONE_CLASSES: Record<Tone, string> = {
  neutral: "bg-white/5 text-muted border-border",
  gold: "bg-gold/10 text-gold-bright border-gold-dim/60",
  win: "bg-win/10 text-win-bright border-win/40",
  loss: "bg-loss/10 text-loss-bright border-loss/40",
  warn: "bg-warn/10 text-warn border-warn/40",
  info: "bg-info/10 text-info border-info/40",
};

export function Badge({ tone = "neutral", className, children }: { tone?: Tone; className?: string; children: React.ReactNode }) {
  return (
    <span
      dir="auto"
      className={cn(
        "inline-flex items-center gap-1 whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px] font-medium",
        TONE_CLASSES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

const COPY_STATUS_TONE: Record<string, Tone> = {
  OBSERVED_ONLY: "info",
  SUBMITTING: "info",
  OPEN: "gold",
  CLOSED_LEADER: "neutral",
  CLOSED_TP: "win",
  CLOSED_LIQ: "loss",
  CLOSED_MANUAL: "neutral",
  CLOSED_RECONCILED: "warn",
  FAILED: "loss",
  UNKNOWN: "warn",
};

const DECISION_TONE: Record<string, Tone> = {
  COPIED: "win",
  FOLLOWED: "gold",
  OBSERVED_ONLY: "info",
  REJECTED: "loss",
  IGNORED_ADD: "neutral",
  IGNORED_REDUCE: "neutral",
  NO_MATCHING_COPY: "neutral",
  FAILED: "loss",
};

const SEVERITY_TONE: Record<string, Tone> = { INFO: "neutral", WARNING: "warn", ERROR: "loss", CRITICAL: "loss" };
const MODE_TONE: Record<string, Tone> = { WATCH: "neutral", OBSERVE: "info", EXECUTE: "gold" };

export function CopyStatusBadge({ status }: { status: string }) {
  return <Badge tone={COPY_STATUS_TONE[status] ?? "neutral"}>{arCopyStatus(status)}</Badge>;
}

export function DecisionBadge({ decision }: { decision: string }) {
  return <Badge tone={DECISION_TONE[decision] ?? "neutral"}>{arDecision(decision)}</Badge>;
}

export function EventTypeBadge({ type }: { type: string }) {
  const tone: Tone = type === "OPEN" ? "gold" : type === "CLOSE" || type === "FLIP" ? "info" : "neutral";
  return <Badge tone={tone}>{arEventType(type)}</Badge>;
}

export function SideBadge({ side }: { side: string | null }) {
  if (!side) return <span className="text-muted-dim">—</span>;
  return <Badge tone={side === "LONG" ? "win" : "loss"}>{arSide(side)}</Badge>;
}

export function SeverityBadge({ severity }: { severity: string }) {
  return <Badge tone={SEVERITY_TONE[severity] ?? "neutral"}>{arSeverity(severity)}</Badge>;
}

export function ModeBadge({ mode }: { mode: string }) {
  return <Badge tone={MODE_TONE[mode] ?? "neutral"}>{arMode(mode)}</Badge>;
}

/** Which copied leader a row belongs to. Loco in gold, every other leader in a neutral tone. */
export function LeaderBadge({ leader }: { leader: string }) {
  return <Badge tone={leader === "loco" ? "gold" : "info"}>{leaderName(leader)}</Badge>;
}
