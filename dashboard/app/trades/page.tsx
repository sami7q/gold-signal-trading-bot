"use client";

import { useState } from "react";
import { Inbox } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Label, Select } from "@/components/ui/input";
import { CopyStatusBadge, SideBadge } from "@/components/ui/badge";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/empty-state";
import { useCopies } from "@/hooks/use-loco";
import { formatDateTime, formatMoney, formatMs, formatNumber, formatPrice, pnlClass } from "@/lib/format";

const FILTERS = [
  { value: "", label: "الكل" },
  { value: "OPEN", label: "مفتوحة" },
  { value: "CLOSED", label: "مغلقة" },
  { value: "CLOSED_TP", label: "هدف الربح" },
  { value: "CLOSED_LIQ", label: "تصفية" },
  { value: "OBSERVED_ONLY", label: "مراقبة فقط" },
  { value: "FAILED", label: "فشلت" },
];

export default function TradesPage() {
  const [status, setStatus] = useState("");
  const query = useCopies(status || undefined, 500);

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-5">
      <div>
        <h1 className="text-xl font-semibold text-bone">صفقاتنا</h1>
        <p className="mt-1 text-sm text-muted">
          كل صفقة نسخها البوت من Loco: سعره مقابل سعرنا، الانزلاق، سرعة التنفيذ، والنتيجة. صفقات &quot;مراقبة
          فقط&quot; محسوبة افتراضيًا ولم تُرسل للوسيط.
        </p>
      </div>

      <Card>
        <div className="flex flex-wrap items-end gap-4 border-b border-border-soft px-4 py-3">
          <div>
            <Label>الحالة</Label>
            <Select value={status} onChange={(e) => setStatus(e.target.value)}>
              {FILTERS.map((f) => (
                <option key={f.value} value={f.value}>{f.label}</option>
              ))}
            </Select>
          </div>
          {query.data ? <p className="pb-1.5 text-xs text-muted-dim">{query.data.length} صفقة</p> : null}
        </div>

        {query.isLoading ? (
          <LoadingState />
        ) : query.isError ? (
          <ErrorState message={(query.error as Error).message} />
        ) : !query.data?.length ? (
          <EmptyState icon={Inbox} title="لا توجد صفقات تطابق هذا الفلتر" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1200px] text-left text-sm">
              <thead>
                <tr className="border-b border-border-soft text-[11px] text-muted-dim">
                  <th className="px-3 py-2 font-medium">#</th>
                  <th className="px-3 py-2 font-medium">الفتح</th>
                  <th className="px-3 py-2 font-medium">الرمز</th>
                  <th className="px-3 py-2 font-medium">الاتجاه</th>
                  <th className="px-3 py-2 font-medium">الحالة</th>
                  <th className="px-3 py-2 text-right font-medium">الربح</th>
                  <th className="px-3 py-2 text-right font-medium">الكمية</th>
                  <th className="px-3 py-2 text-right font-medium">سعر Loco</th>
                  <th className="px-3 py-2 text-right font-medium">سعرنا</th>
                  <th className="px-3 py-2 text-right font-medium">الانزلاق</th>
                  <th className="px-3 py-2 text-right font-medium">التنفيذ</th>
                  <th className="px-3 py-2 text-right font-medium">الهدف</th>
                  <th className="px-3 py-2 text-right font-medium">الخروج</th>
                  <th className="px-3 py-2 font-medium">الإغلاق</th>
                </tr>
              </thead>
              <tbody className="font-data whitespace-nowrap">
                {query.data.map((c) => (
                  <tr key={c.id} className="border-b border-border-soft/60 last:border-0 hover:bg-white/[0.02]">
                    <td className="px-3 py-2 text-muted">#{c.id}</td>
                    <td className="px-3 py-2 text-muted">{formatDateTime(c.opened_at)}</td>
                    <td className="px-3 py-2 text-bone">{c.symbol}</td>
                    <td className="px-3 py-2"><SideBadge side={c.side} /></td>
                    <td className="px-3 py-2"><CopyStatusBadge status={c.status} /></td>
                    <td className={`px-3 py-2 text-right ${pnlClass(c.realized_pnl)}`}>{c.closed_at ? formatMoney(c.realized_pnl) : "—"}</td>
                    <td className="px-3 py-2 text-right text-bone">{formatNumber(c.qty_initial, 6)}</td>
                    <td className="px-3 py-2 text-right text-muted">{formatPrice(c.leader_entry_px)}</td>
                    <td className="px-3 py-2 text-right text-bone">{formatPrice(c.entry_price)}</td>
                    <td className="px-3 py-2 text-right text-muted">{c.slippage_bps !== null ? `${formatNumber(c.slippage_bps, 1)} bps` : "—"}</td>
                    <td className="px-3 py-2 text-right text-muted">{formatMs(c.latency_order_ms)}</td>
                    <td className="px-3 py-2 text-right text-win-bright">{formatPrice(c.tp_price)}</td>
                    <td className="px-3 py-2 text-right text-bone">{formatPrice(c.exit_price_avg)}</td>
                    <td className="px-3 py-2 text-muted">{formatDateTime(c.closed_at)}</td>
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
