"use client";

import { Fragment, useState } from "react";
import { ChevronDown, ChevronRight, Radio } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Select, Label } from "@/components/ui/input";
import { StatusBadge } from "@/components/ui/badge";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/empty-state";
import { useSignals } from "@/hooks/use-signals";
import { formatDateTime, formatPrice, cn } from "@/lib/format";
import type { SignalStatus } from "@/types/api";

const STATUS_OPTIONS: Array<{ value: SignalStatus | "ALL"; label: string }> = [
  { value: "ALL", label: "All statuses" },
  { value: "WAITING_FOR_DETAILS", label: "Waiting for details" },
  { value: "READY", label: "Ready" },
  { value: "EXECUTED", label: "Executed" },
  { value: "REJECTED", label: "Rejected" },
  { value: "EXPIRED", label: "Expired" },
  { value: "DUPLICATE", label: "Duplicate" },
  { value: "IGNORED", label: "Ignored" },
];

export default function SignalsPage() {
  const [status, setStatus] = useState<SignalStatus | "ALL">("ALL");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const signalsQuery = useSignals(status === "ALL" ? undefined : status);

  function toggle(id: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-5">
      <div>
        <h1 className="text-xl font-semibold text-bone">Signals</h1>
        <p className="mt-1 text-sm text-muted">
          Every parsed message and what the state machine decided. Expand a row to see the exact
          source text the parser saw.
        </p>
      </div>

      <Card>
        <div className="flex flex-wrap items-end gap-4 border-b border-border-soft px-4 py-3">
          <div>
            <Label>Status</Label>
            <Select value={status} onChange={(e) => setStatus(e.target.value as SignalStatus | "ALL")}>
              {STATUS_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </Select>
          </div>
        </div>

        {signalsQuery.isLoading ? (
          <LoadingState />
        ) : signalsQuery.isError ? (
          <ErrorState message={(signalsQuery.error as Error).message} />
        ) : !signalsQuery.data?.length ? (
          <EmptyState
            icon={Radio}
            title="No signals yet"
            description="Send a mock BUY GOLD message from the Simulation page to see the parser in action."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-left text-sm">
              <thead>
                <tr className="border-b border-border-soft text-[11px] uppercase tracking-wide text-muted-dim">
                  <th className="w-8 px-3 py-2" />
                  <th className="px-3 py-2 font-medium">ID</th>
                  <th className="px-3 py-2 font-medium">Received</th>
                  <th className="px-3 py-2 font-medium">Action</th>
                  <th className="px-3 py-2 font-medium">Symbol</th>
                  <th className="px-3 py-2 text-right font-medium">Stop Loss</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                  <th className="px-3 py-2 font-medium">Rejection</th>
                  <th className="px-3 py-2 font-medium">Trade</th>
                </tr>
              </thead>
              <tbody className="font-data">
                {signalsQuery.data.map((signal) => {
                  const isOpen = expanded.has(signal.id);
                  return (
                    <Fragment key={signal.id}>
                      <tr
                        onClick={() => toggle(signal.id)}
                        className="cursor-pointer border-b border-border-soft/60 last:border-0 hover:bg-white/[0.02]"
                      >
                        <td className="px-3 py-2 text-muted-dim">
                          {isOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                        </td>
                        <td className="px-3 py-2 text-muted">#{signal.id}</td>
                        <td className="px-3 py-2 text-muted">{formatDateTime(signal.first_message_at)}</td>
                        <td className="px-3 py-2 text-bone">
                          {signal.side} {signal.signal_type !== "MARKET" ? signal.signal_type : ""}
                        </td>
                        <td className="px-3 py-2 text-bone">{signal.symbol}</td>
                        <td className="px-3 py-2 text-right text-bone">{formatPrice(signal.channel_stop_loss)}</td>
                        <td className="px-3 py-2">
                          <StatusBadge status={signal.status} />
                        </td>
                        <td className="px-3 py-2 text-muted">{signal.rejection_reason ?? "—"}</td>
                        <td className="px-3 py-2 text-gold">{signal.linked_trade_id ? `#${signal.linked_trade_id}` : "—"}</td>
                      </tr>
                      {isOpen && (
                        <tr className="border-b border-border-soft/60 bg-ink/40 last:border-0">
                          <td />
                          <td colSpan={8} className="px-3 py-3">
                            {signal.messages.length === 0 ? (
                              <p className="text-xs text-muted-dim">No linked source messages.</p>
                            ) : (
                              <ul className="flex flex-col gap-2">
                                {signal.messages.map((m, i) => (
                                  <li
                                    key={i}
                                    className={cn(
                                      "rounded border border-border-soft bg-surface px-3 py-2",
                                    )}
                                  >
                                    <div className="mb-1 flex items-center gap-2 text-[11px] uppercase tracking-wide text-muted-dim">
                                      <span className="text-gold-dim">{m.role}</span>
                                      <span>·</span>
                                      <span>{formatDateTime(m.sent_at)}</span>
                                    </div>
                                    <pre className="whitespace-pre-wrap text-xs text-bone">{m.raw_text}</pre>
                                  </li>
                                ))}
                              </ul>
                            )}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
