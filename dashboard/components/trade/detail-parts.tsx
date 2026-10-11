import { cn, formatNumber, formatPrice, formatUsd } from "@/lib/format";
import type { LeaderOrder } from "@/types/api";

/** Keeps a signed number ("-0.44 bps", "+2.50%") in one piece inside right-to-left Arabic text. */
export const ltr = (s: string) => `⁦${s}⁩`;

const ARABIC = /[؀-ۿ]/;

/** One labelled figure inside a detail section. `hint` is the small line that explains or qualifies it. */
export function Fact({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  tone?: "default" | "win" | "loss" | "warn" | "strong";
}) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-border-soft/60 py-2 last:border-0">
      <span className="shrink-0 whitespace-nowrap pt-0.5 text-xs text-muted">{label}</span>
      <span className="text-end">
        <span
          dir={typeof value === "string" && ARABIC.test(value) ? "rtl" : "ltr"}
          className={cn(
            "font-data text-sm",
            tone === "win" && "text-win-bright",
            tone === "loss" && "text-loss-bright",
            tone === "warn" && "text-warn",
            tone === "strong" && "font-semibold text-bone",
            tone === "default" && "text-bone",
          )}
        >
          {value}
        </span>
        {hint ? <span className="block text-[11px] leading-snug text-muted-dim">{hint}</span> : null}
      </span>
    </div>
  );
}

export function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-md border border-border-soft bg-ink/40">
      <h3 className="border-b border-border-soft px-3 py-2 text-xs font-semibold text-gold-bright">{title}</h3>
      <div className="px-3">{children}</div>
    </section>
  );
}

export function Banner({ warn, children }: { warn: boolean; children: React.ReactNode }) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 text-sm",
        warn ? "border-warn/40 bg-warn/10 text-warn" : "border-border-soft bg-white/[0.02] text-bone",
      )}
    >
      {children}
    </div>
  );
}

/** "حجم 0.5 BTC · متوسط 83,933 · $41,966 · 15 قطعة" -- Loco's whole order, however many pieces it filled in. */
export function leaderOrderLine(o: LeaderOrder | null, coin: string): string {
  if (!o) return "—";
  const pieces = o.fills > 1 ? ` · ${o.fills} قطعة` : "";
  return `حجم ${ltr(`${formatNumber(o.size, 8)} ${coin}`)} · متوسط ${formatPrice(o.avg_px)} · ${ltr(formatUsd(o.notional))}${pieces}`;
}

/**
 * The detail panels sit inside wide, horizontally scrolling tables. This keeps a panel pinned to the visible width
 * of that scroll area (an `@container`), so nothing in it hides off-screen, and lays it out right-to-left: it is
 * prose, unlike the numeric tables around it.
 */
export function DetailFrame({ children }: { children: React.ReactNode }) {
  return (
    <div dir="rtl" className="sticky left-0 w-[100cqw] border-t border-border-soft bg-surface/60 px-4 py-4 text-start font-sans">
      {children}
    </div>
  );
}

/** Loco's position before → after an order, signed (negative = short). */
export function positionChange(o: LeaderOrder | null): string {
  if (!o) return "—";
  return ltr(`${formatNumber(o.position_before, 8)} → ${formatNumber(o.position_after, 8)}`);
}
