"use client";

import { useMemo, useState } from "react";
import { Inbox } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Select, Input, Label } from "@/components/ui/input";
import { ResultBadge, TradeStatusBadge } from "@/components/ui/badge";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/empty-state";
import { useTrades, type TradeFilters } from "@/hooks/use-trades";
import { formatDateTime, formatMoney, formatPrice } from "@/lib/format";
import type { TradeResult, TradeStatus } from "@/types/api";

const STATUS_OPTIONS: Array<{ value: TradeStatus | "ALL"; label: string }> = [
  { value: "ALL", label: "All statuses" },
  { value: "OPEN", label: "Open" },
  { value: "CLOSED_TP", label: "Closed — TP" },
  { value: "CLOSED_SL", label: "Closed — SL" },
  { value: "CLOSED_MANUAL", label: "Closed — Manual" },
  { value: "REJECTED", label: "Rejected" },
  { value: "FAILED", label: "Failed" },
];

const RESULT_OPTIONS: Array<{ value: TradeResult | "ALL"; label: string }> = [
  { value: "ALL", label: "Any result" },
  { value: "WIN", label: "Win" },
  { value: "LOSS", label: "Loss" },
  { value: "BREAKEVEN", label: "Breakeven" },
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
        <h1 className="text-xl font-semibold text-bone">Trades</h1>
        <p className="mt-1 text-sm text-muted">Every trade the engine created, including rejected attempts.</p>
      </div>

      <Card>
        <div className="flex flex-wrap items-end gap-4 border-b border-border-soft px-4 py-3">
          <div>
            <Label>Status</Label>
            <Select value={status} onChange={(e) => setStatus(e.target.value as TradeStatus | "ALL")}>
              {STATUS_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label>Result</Label>
            <Select value={result} onChange={(e) => setResult(e.target.value as TradeResult | "ALL")}>
              {RESULT_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label>From</Label>
            <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="w-40" />
          </div>
          <div>
            <Label>To</Label>
            <Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="w-40" />
          </div>
        </div>

        {tradesQuery.isLoading ? (
          <LoadingState />
        ) : tradesQuery.isError ? (
          <ErrorState message={(tradesQuery.error as Error).message} />
        ) : !tradesQuery.data?.length ? (
          <EmptyState icon={Inbox} title="No trades match these filters" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1100px] text-left text-sm">
              <thead>
                <tr className="border-b border-border-soft text-[11px] uppercase tracking-wide text-muted-dim">
                  <th className="px-3 py-2 font-medium">ID</th>
                  <th className="px-3 py-2 font-medium">Opened</th>
                  <th className="px-3 py-2 font-medium">Closed</th>
                  <th className="px-3 py-2 font-medium">Symbol</th>
                  <th className="px-3 py-2 font-medium">Side</th>
                  <th className="px-3 py-2 text-right font-medium">Lot</th>
                  <th className="px-3 py-2 text-right font-medium">Entry</th>
                  <th className="px-3 py-2 text-right font-medium">SL</th>
                  <th className="px-3 py-2 text-right font-medium">TP</th>
                  <th className="px-3 py-2 text-right font-medium">Target Pips</th>
                  <th className="px-3 py-2 text-right font-medium">Exit</th>
                  <th className="px-3 py-2 text-right font-medium">Profit</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                  <th className="px-3 py-2 font-medium">Result</th>
                </tr>
              </thead>
              <tbody className="font-data">
                {tradesQuery.data.map((trade) => (
                  <tr key={trade.id} className="border-b border-border-soft/60 last:border-0 hover:bg-white/[0.02]">
                    <td className="px-3 py-2 text-muted">#{trade.id}</td>
                    <td className="px-3 py-2 text-muted">{formatDateTime(trade.opened_at)}</td>
                    <td className="px-3 py-2 text-muted">{formatDateTime(trade.closed_at)}</td>
                    <td className="px-3 py-2 text-bone">{trade.symbol}</td>
                    <td className="px-3 py-2 text-bone">{trade.side}</td>
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
