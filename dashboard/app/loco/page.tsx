"use client";

import { useState } from "react";
import { Radar } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/card";
import { Badge, CopyStatusBadge, SideBadge } from "@/components/ui/badge";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/empty-state";
import { useLeaderPositions, useLeaders } from "@/hooks/use-loco";
import { cn, formatMoney, formatNumber, formatPrice, formatRelativeTime, formatUsd, leaderName, leaderTitle, pnlClass } from "@/lib/format";
import { urlParam } from "@/lib/url";

export default function PositionsPage() {
  const leaders = useLeaders();
  const [picked, setPicked] = useState<string | null>(() => urlParam("leader"));
  const labels = leaders.data?.map((l) => l.label) ?? [];
  const selected = picked && labels.includes(picked) ? picked : (labels[0] ?? null);
  const query = useLeaderPositions(selected);
  const data = query.data;
  const name = leaderName(selected);

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-5">
      <div>
        <h1 className="text-xl font-semibold text-bone">مراكز المتداولين</h1>
        <p className="mt-1 text-sm text-muted">
          مراكز كل متداول المفتوحة الآن على Hyperliquid، وبجانب كل منها صفقتنا المقابلة. المراكز التي كانت مفتوحة قبل
          إضافة المتداول لا تُنسخ أبدًا — البوت ينسخ فقط ما يفتحه بعد ذلك.
        </p>
      </div>

      {labels.length > 1 ? (
        <div role="tablist" aria-label="المتداول" className="flex flex-wrap gap-1.5">
          {leaders.data?.map((l) => (
            <button
              key={l.label}
              type="button"
              role="tab"
              aria-selected={l.label === selected}
              onClick={() => setPicked(l.label)}
              className={cn(
                "rounded-md border px-3 py-1.5 text-sm transition-colors",
                l.label === selected ? "border-gold-dim bg-gold/10 text-gold-bright" : "border-border text-muted hover:text-bone",
              )}
            >
              {leaderName(l.label)}
              <span className="ms-2 font-data text-xs text-muted-dim">{l.leader_positions}</span>
            </button>
          ))}
        </div>
      ) : null}

      <Card>
        <CardHeader
          title={
            <span className="flex flex-wrap items-baseline gap-2">
              <span>{selected ? leaderTitle(selected) : "—"}</span>
              <span className="font-data text-xs font-normal text-muted">{data?.address ?? ""}</span>
            </span>
          }
          subtitle={data ? `قيمة الحساب ${formatUsd(data.account_value)} · آخر لقطة ${formatRelativeTime(data.snapshot_at)}` : undefined}
        />
        {leaders.isLoading || query.isLoading ? (
          <LoadingState />
        ) : query.isError ? (
          <ErrorState message={(query.error as Error).message} />
        ) : !data?.rows.length ? (
          <EmptyState icon={Radar} title={`${name} ليس لديه مراكز مفتوحة الآن`} />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[980px] text-left text-sm">
              <thead>
                <tr className="border-b border-border-soft text-[11px] text-muted-dim">
                  <th className="px-3 py-2 font-medium">العملة</th>
                  <th className="px-3 py-2 font-medium">اتجاهه</th>
                  <th className="px-3 py-2 text-right font-medium">حجمه</th>
                  <th className="px-3 py-2 text-right font-medium">دخوله</th>
                  <th className="px-3 py-2 font-medium">قابل للنسخ</th>
                  <th className="px-3 py-2 font-medium">صفقتنا</th>
                  <th className="px-3 py-2 text-right font-medium">كميتنا</th>
                  <th className="px-3 py-2 text-right font-medium">دخولنا</th>
                  <th className="px-3 py-2 text-right font-medium">هدف الربح</th>
                  <th className="px-3 py-2 text-right font-medium">محقق</th>
                </tr>
              </thead>
              <tbody className="font-data whitespace-nowrap">
                {data.rows.map((row) => (
                  <tr key={row.coin} className="border-b border-border-soft/60 last:border-0 hover:bg-white/[0.02]">
                    <td className="px-3 py-2 text-bone">{row.coin}</td>
                    <td className="px-3 py-2"><SideBadge side={row.side} /></td>
                    <td className="px-3 py-2 text-right text-bone">{formatNumber(row.size, 4)}</td>
                    <td className="px-3 py-2 text-right text-bone">{formatPrice(row.entry_px)}</td>
                    <td className="px-3 py-2 font-sans">
                      {row.copyable ? <Badge tone="win">نعم</Badge> : <Badge tone="neutral">سابق — لا</Badge>}
                    </td>
                    <td className="px-3 py-2">{row.ours ? <CopyStatusBadge status={row.ours.status} /> : <span className="text-muted-dim">—</span>}</td>
                    <td className="px-3 py-2 text-right text-bone">{row.ours ? formatNumber(row.ours.qty_open, 6) : "—"}</td>
                    <td className="px-3 py-2 text-right text-bone">{formatPrice(row.ours?.entry_price)}</td>
                    <td className="px-3 py-2 text-right text-win-bright">{formatPrice(row.ours?.tp_price)}</td>
                    <td className={`px-3 py-2 text-right ${pnlClass(row.ours?.realized_pnl)}`}>
                      {row.ours ? formatMoney(row.ours.realized_pnl) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
