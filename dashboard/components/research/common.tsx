"use client";

// botup.md §V9 -- shared bits of the research pages (top 10, mixes, reports).

import { FileSearch } from "lucide-react";
import { Card } from "@/components/ui/card";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/empty-state";
import { useResearch } from "@/hooks/use-loco";
import { isNotFound } from "@/lib/api";
import { ServerOutdated } from "@/components/ui/server-outdated";
import { cn, formatNumber } from "@/lib/format";
import type { ResearchData } from "@/types/api";

/** ×3.42 -> "+242%" (a year's multiple as a percentage return). */
export function multPct(m: number | null | undefined, digits = 0): string {
  if (m === null || m === undefined) return "—";
  const p = (m - 1) * 100;
  return `${p >= 0 ? "+" : ""}${formatNumber(p, digits)}%`;
}

/** Whole dollars: research figures are simulations, cents are noise. */
export function usd0(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return `$${Math.round(v).toLocaleString("en-US")}`;
}

export function multClass(m: number | null | undefined): string {
  if (m === null || m === undefined) return "text-muted";
  return m >= 1 ? "text-win-bright" : "text-loss-bright";
}

export function pct(v: number | null | undefined, digits = 0): string {
  return v === null || v === undefined ? "—" : `${formatNumber(v * 100, digits)}%`;
}

export function Th({ children, end, className }: { children?: React.ReactNode; end?: boolean; className?: string }) {
  return <th className={cn("px-3 py-2 font-medium", end && "text-right", className)}>{children}</th>;
}

export function WalletChip({ label, address }: { label: string; address?: string }) {
  const known: Record<string, string> = { "8bae": "الثلاثي", "8fc7": "الثلاثي", "69b0": "الثلاثي", f97a: "Loco" };
  const inner = (
    <span className="inline-flex items-center gap-1.5 rounded border border-border bg-white/5 px-1.5 py-0.5 font-data text-xs text-bone">
      0x{label}
      {known[label] ? <span className="font-sans text-[10px] text-gold-bright">{known[label]}</span> : null}
    </span>
  );
  return address ? (
    <a href={`https://hypurrscan.io/address/${address}`} target="_blank" rel="noreferrer" className="hover:underline">
      {inner}
    </a>
  ) : inner;
}

export function ResearchGate({ children }: { children: (d: ResearchData) => React.ReactNode }) {
  const q = useResearch();
  if (q.isLoading) return <LoadingState />;
  if (isNotFound(q.error)) return <ServerOutdated what="صفحات البحث" />;
  if (q.isError || !q.data) return <ErrorState message={(q.error as Error | null)?.message ?? "—"} />;
  if (!q.data.available) {
    return (
      <Card>
        <EmptyState
          icon={FileSearch}
          title="لا نتائج بحث على السيرفر بعد"
          description="شغّل البحث في مجلد «بحث عن متداولين» (run_research_x2.bat) ثم: loco-copy research-import reports\\bot\\research_latest.json"
        />
      </Card>
    );
  }
  return <>{children(q.data)}</>;
}
