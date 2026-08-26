"use client";

import Link from "next/link";
import { StatCard } from "@/components/ui/stat-card";
import { Card, CardHeader } from "@/components/ui/card";
import { TradeStatusBadge, ResultBadge, SeverityBadge } from "@/components/ui/badge";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/empty-state";
import { useDashboardSummary, useRecentEvents, useRecentTrades } from "@/hooks/use-dashboard";
import { formatDateTime, formatMoney, formatPercent, formatPrice, formatTime } from "@/lib/format";
import { Inbox, ScrollText } from "lucide-react";

export default function OverviewPage() {
  const summaryQuery = useDashboardSummary();
  const tradesQuery = useRecentTrades(8);
  const eventsQuery = useRecentEvents(10);

  const summary = summaryQuery.data?.summary;
  const stats = summary?.statistics;

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-bone">Overview</h1>
        <p className="mt-1 text-sm text-muted">
          Everything the bot has done, at a glance. Currently running against{" "}
          {summaryQuery.data?.settings.mode === "SIMULATION" ? "the paper broker" : "a connected broker"}.
        </p>
      </div>

      {summaryQuery.isLoading ? (
        <LoadingState />
      ) : summaryQuery.isError ? (
        <ErrorState message={(summaryQuery.error as Error).message} />
      ) : (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatCard label="Balance" value={`$${formatPrice(summary?.balance)}`} tone="gold" />
          <StatCard
            label="Today's P/L"
            value={formatMoney(stats?.today_pl)}
            tone={Number(stats?.today_pl ?? 0) > 0 ? "win" : Number(stats?.today_pl ?? 0) < 0 ? "loss" : "default"}
            sub={`${stats?.today_trades ?? 0} trades today`}
          />
          <StatCard label="Open Trades" value={stats?.open_trades ?? 0} />
          <StatCard label="Total Trades" value={stats?.trades_executed ?? 0} />
          <StatCard label="Wins" value={stats?.wins ?? 0} tone="win" />
          <StatCard label="Losses" value={stats?.losses ?? 0} tone="loss" />
          <StatCard label="Win Rate" value={formatPercent(stats?.win_rate)} tone="gold" />
          <StatCard
            label="Net Profit"
            value={formatMoney(stats?.net_profit)}
            tone={Number(stats?.net_profit ?? 0) > 0 ? "win" : Number(stats?.net_profit ?? 0) < 0 ? "loss" : "default"}
          />
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
        <Card className="lg:col-span-3">
          <CardHeader
            title="Recent Trades"
            subtitle="Most recently created, newest first"
            action={
              <Link href="/trades" className="text-xs text-gold hover:text-gold-bright">
                View all →
              </Link>
            }
          />
          {tradesQuery.isLoading ? (
            <LoadingState />
          ) : !tradesQuery.data?.length ? (
            <EmptyState icon={Inbox} title="No trades yet" description="Send a mock signal from the Simulation page to see one here." />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-border-soft text-[11px] uppercase tracking-wide text-muted-dim">
                    <th className="px-4 py-2 font-medium">Opened</th>
                    <th className="px-4 py-2 font-medium">Side</th>
                    <th className="px-4 py-2 font-medium text-right">Lot</th>
                    <th className="px-4 py-2 font-medium text-right">Entry</th>
                    <th className="px-4 py-2 font-medium text-right">Profit</th>
                    <th className="px-4 py-2 font-medium">Status</th>
                    <th className="px-4 py-2 font-medium">Result</th>
                  </tr>
                </thead>
                <tbody className="font-data">
                  {tradesQuery.data.map((trade) => (
                    <tr key={trade.id} className="border-b border-border-soft/60 last:border-0">
                      <td className="px-4 py-2 text-muted">{formatDateTime(trade.opened_at ?? trade.created_at)}</td>
                      <td className="px-4 py-2 text-bone">{trade.side}</td>
                      <td className="px-4 py-2 text-right text-bone">{formatPrice(trade.executed_lot ?? trade.requested_lot)}</td>
                      <td className="px-4 py-2 text-right text-bone">{formatPrice(trade.entry_price)}</td>
                      <td
                        className={
                          "px-4 py-2 text-right " +
                          (Number(trade.profit ?? 0) > 0
                            ? "text-win-bright"
                            : Number(trade.profit ?? 0) < 0
                              ? "text-loss-bright"
                              : "text-muted")
                        }
                      >
                        {trade.profit !== null ? formatMoney(trade.profit) : "—"}
                      </td>
                      <td className="px-4 py-2">
                        <TradeStatusBadge status={trade.status} />
                      </td>
                      <td className="px-4 py-2">
                        <ResultBadge result={trade.result} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader
            title="Recent Events"
            subtitle="Live audit trail"
            action={
              <Link href="/logs" className="text-xs text-gold hover:text-gold-bright">
                View all →
              </Link>
            }
          />
          {eventsQuery.isLoading ? (
            <LoadingState />
          ) : !eventsQuery.data?.length ? (
            <EmptyState icon={ScrollText} title="No events yet" />
          ) : (
            <ul className="divide-y divide-border-soft/60">
              {eventsQuery.data.map((event) => (
                <li key={event.id} className="flex items-start justify-between gap-3 px-4 py-2.5">
                  <div className="min-w-0">
                    <p className="truncate text-sm text-bone">{event.message}</p>
                    <p className="font-data mt-0.5 text-[11px] text-muted-dim">
                      {formatTime(event.created_at)} · {event.event_type}
                    </p>
                  </div>
                  <SeverityBadge severity={event.severity} />
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
