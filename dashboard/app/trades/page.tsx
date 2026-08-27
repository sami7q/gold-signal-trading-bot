"use client";

import { useMemo, useState } from "react";
import { Inbox } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Select, Input, Label } from "@/components/ui/input";
import { ResultBadge, TradeStatusBadge } from "@/components/ui/badge";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/empty-state";
import { useTrades, type TradeFilters } from "@/hooks/use-trades";
import { arSide, formatDateTime, formatMoney, formatPrice } from "@/lib/format";
import type { TradeResult, TradeStatus } from "@/types/api";

const STATUS_OPTIONS: Array<{ value: TradeStatus | "ALL"; label: string }> = [
  { value: "ALL", label: "كل الحالات" },
  { value: "OPEN", label: "مفتوحة" },
  { value: "CLOSED_TP", label: "مغلقة — هدف ربح" },
  { value: "CLOSED_SL", label: "مغلقة — وقف خسارة" },
  { value: "CLOSED_MANUAL", label: "مغلقة — يدويًا" },
  { value: "REJECTED", label: "مرفوضة" },
  { value: "FAILED", label: "فشلت" },
];

const RESULT_OPTIONS: Array<{ value: TradeResult | "ALL"; label: string }> = [
  { value: "ALL", label: "أي نتيجة" },
  { value: "WIN", label: "ربح" },
  { value: "LOSS", label: "خسارة" },
  { value: "BREAKEVEN", label: "تعادل" },
];

export default function TradesPage() {
  const [status, setStatus] = useState<TradeStatus | "ALL">("ALL");
  const [result, setResult] = useState<TradeResult | "ALL">("ALL");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  const filters: TradeFilters = useMemo(
    () => ({
      status: status === "ALL" ? undefined : status,
      result: result === "ALL" ? undefined : result,
      dateFrom: dateFrom ? new Date(dateFrom).toISOString() : undefined,
      dateTo: dateTo ? new Date(dateTo).toISOString() : undefined,
    }),
    [status, result, dateFrom, dateTo],
  );

  const tradesQuery = useTrades(filters);

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-5">
      <div>
        <h1 className="text-xl font-semibold text-bone">الصفقات</h1>
        <p className="mt-1 text-sm text-muted">كل صفقة أنشأها المحرك، بما فيها المحاولات المرفوضة.</p>
      </div>

      <Card>
        <div className="flex flex-wrap items-end gap-4 border-b border-border-soft px-4 py-3">
          <div>
            <Label>الحالة</Label>
            <Select value={status} onChange={(e) => setStatus(e.target.value as TradeStatus | "ALL")}>
              {STATUS_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label>النتيجة</Label>
            <Select value={result} onChange={(e) => setResult(e.target.value as TradeResult | "ALL")}>
              {RESULT_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label>من</Label>
            <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="w-40" />
          </div>
          <div>
            <Label>إلى</Label>
            <Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="w-40" />
          </div>
        </div>

        {tradesQuery.isLoading ? (
          <LoadingState />
        ) : tradesQuery.isError ? (
          <ErrorState message={(tradesQuery.error as Error).message} />
        ) : !tradesQuery.data?.length ? (
          <EmptyState icon={Inbox} title="لا توجد صفقات تطابق هذه الفلاتر" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1100px] text-left text-sm">
              <thead>
                <tr className="border-b border-border-soft text-[11px] uppercase tracking-wide text-muted-dim">
                  <th className="px-3 py-2 font-medium">الرقم</th>
                  <th className="px-3 py-2 font-medium">الفتح</th>
                  <th className="px-3 py-2 font-medium">الإغلاق</th>
                  <th className="px-3 py-2 font-medium">الرمز</th>
                  <th className="px-3 py-2 font-medium">الاتجاه</th>
                  <th className="px-3 py-2 text-right font-medium">اللوت</th>
                  <th className="px-3 py-2 text-right font-medium">الدخول</th>
                  <th className="px-3 py-2 text-right font-medium">وقف الخسارة</th>
                  <th className="px-3 py-2 text-right font-medium">هدف الربح</th>
                  <th className="px-3 py-2 text-right font-medium">النقاط المستهدفة</th>
                  <th className="px-3 py-2 text-right font-medium">الخروج</th>
                  <th className="px-3 py-2 text-right font-medium">الربح</th>
                  <th className="px-3 py-2 font-medium">الحالة</th>
                  <th className="px-3 py-2 font-medium">النتيجة</th>
                </tr>
              </thead>
              <tbody className="font-data">
                {tradesQuery.data.map((trade) => (
                  <tr key={trade.id} className="border-b border-border-soft/60 last:border-0 hover:bg-white/[0.02]">
                    <td className="px-3 py-2 text-muted">#{trade.id}</td>
                    <td className="px-3 py-2 text-muted">{formatDateTime(trade.opened_at)}</td>
                    <td className="px-3 py-2 text-muted">{formatDateTime(trade.closed_at)}</td>
                    <td className="px-3 py-2 text-bone">{trade.symbol}</td>
                    <td className="px-3 py-2 text-bone">{arSide(trade.side)}</td>
                    <td className="px-3 py-2 text-right text-bone">{formatPrice(trade.executed_lot ?? trade.requested_lot)}</td>
                    <td className="px-3 py-2 text-right text-bone">{formatPrice(trade.entry_price)}</td>
                    <td className="px-3 py-2 text-right text-loss-bright">{formatPrice(trade.stop_loss)}</td>
                    <td className="px-3 py-2 text-right text-win-bright">{formatPrice(trade.take_profit_price)}</td>
                    <td className="px-3 py-2 text-right text-bone">{formatPrice(trade.target_pips)}</td>
                    <td className="px-3 py-2 text-right text-bone">{formatPrice(trade.exit_price)}</td>
                    <td
                      className={
                        "px-3 py-2 text-right " +
                        (Number(trade.profit ?? 0) > 0
                          ? "text-win-bright"
                          : Number(trade.profit ?? 0) < 0
                            ? "text-loss-bright"
                            : "text-muted")
                      }
                    >
                      {trade.profit !== null ? formatMoney(trade.profit) : "—"}
                    </td>
                    <td className="px-3 py-2">
                      <TradeStatusBadge status={trade.status} />
                    </td>
                    <td className="px-3 py-2">
                      <ResultBadge result={trade.result} />
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
