"use client";

import { Radar } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/card";
import { Badge, CopyStatusBadge, SideBadge } from "@/components/ui/badge";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/empty-state";
import { useLocoPositions } from "@/hooks/use-loco";
import { formatMoney, formatNumber, formatPrice, formatRelativeTime, formatUsd, pnlClass } from "@/lib/format";

export default function LocoPage() {
  const query = useLocoPositions();
  const data = query.data;

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-5">
      <div>
        <h1 className="text-xl font-semibold text-bone">مراكز Loco</h1>
        <p className="mt-1 text-sm text-muted">
          مراكز Loco المفتوحة الآن على Hyperliquid، وبجانب كل منها صفقتنا المقابلة. المراكز التي كانت مفتوحة قبل
          تشغيل البوت لا تُنسخ أبدًا — البوت ينسخ فقط ما يفتحه Loco بعد التشغيل.
        </p>
      </div>

      <Card>
        <CardHeader
          title={<span className="font-data">{data?.address ?? "—"}</span>}
          subtitle={data ? `قيمة الحساب ${formatUsd(data.account_value)} · آخر لقطة ${formatRelativeTime(data.snapshot_at)}` : undefined}
        />
        {query.isLoading ? (
          <LoadingState />
        ) : query.isError ? (
          <ErrorState message={(query.error as Error).message} />
        ) : !data?.rows.length ? (
          <EmptyState icon={Radar} title="Loco ليس لديه مراكز مفتوحة الآن" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[980px] text-left text-sm">
              <thead>
                <tr className="border-b border-border-soft text-[11px] text-muted-dim">
                  <th className="px-3 py-2 font-medium">العملة</th>
                  <th className="px-3 py-2 font-medium">اتجاه Loco</th>
                  <th className="px-3 py-2 text-right font-medium">حجم Loco</th>
                  <th className="px-3 py-2 text-right font-medium">دخول Loco</th>
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
