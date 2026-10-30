import { cn } from "@/lib/format";

export function StatCard({
  label,
  value,
  sub,
  tone = "default",
  className,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  tone?: "default" | "gold" | "win" | "loss";
  className?: string;
}) {
  const toneClass =
    tone === "gold"
      ? "text-gold-bright"
      : tone === "win"
        ? "text-win-bright"
        : tone === "loss"
          ? "text-loss-bright"
          : "text-bone";

  return (
    <div className={cn("glass rounded-2xl p-4", className)}>
      <div className="text-[11px] font-medium text-muted">{label}</div>
      <div className={cn("font-data mt-1.5 text-2xl font-medium leading-none", toneClass)}>{value}</div>
      {sub ? <div className="mt-1.5 text-xs text-muted">{sub}</div> : null}
    </div>
  );
}
