"use client";

import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  BarChart,
  Bar,
  Legend,
} from "recharts";
import { LineChart as LineChartIcon } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/card";
import { StatCard } from "@/components/ui/stat-card";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/empty-state";
import { useDashboardSummary } from "@/hooks/use-dashboard";
import { useDailyPerformance, useProfitCurve } from "@/hooks/use-analytics";
import { formatMoney, formatPercent, formatTime } from "@/lib/format";

const CHART_TEXT = "#8b8f98";
const GOLD = "#c9a227";
const WIN = "#4f9d69";
const LOSS = "#c1554a";

export default function AnalyticsPage() {
  const summaryQuery = useDashboardSummary();
  const profitCurveQuery = useProfitCurve();
  const dailyQuery = useDailyPerformance();

  const stats = summaryQuery.data?.summary.statistics;

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-bone">Analytics</h1>
        <p className="mt-1 text-sm text-muted">Performance derived entirely from closed trades.</p>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="Win Rate" value={formatPercent(stats?.win_rate)} tone="gold" />
        <StatCard label="Average Win" value={formatMoney(stats?.average_win)} tone="win" />
        <StatCard label="Average Loss" value={formatMoney(stats?.average_loss)} tone="loss" />
        <StatCard
          label="Best / Worst"
          value={
            <span className="text-base">
              <span className="text-win-bright">{formatMoney(stats?.best_trade)}</span>
              {" / "}
              <span className="text-loss-bright">{formatMoney(stats?.worst_trade)}</span>
            </span>
          }
        />
      </div>

      <Card>
        <CardHeader title="Profit Curve" subtitle="Cumulative profit across closed trades" />
        <div className="h-72 p-4 pl-0">
          {profitCurveQuery.isLoading ? (
            <LoadingState />
          ) : profitCurveQuery.isError ? (
            <ErrorState message={(profitCurveQuery.error as Error).message} />
          ) : !profitCurveQuery.data?.length ? (
            <EmptyState icon={LineChartIcon} title="No closed trades yet" />
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={profitCurveQuery.data} margin={{ top: 10, right: 20, left: 10, bottom: 0 }}>
                <defs>
                  <linearGradient id="profitFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={GOLD} stopOpacity={0.35} />
                    <stop offset="100%" stopColor={GOLD} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="#262b33" vertical={false} />
                <XAxis
                  dataKey="closed_at"
                  tickFormatter={(v) => formatTime(v)}
                  stroke={CHART_TEXT}
                  fontSize={11}
                  tickLine={false}
                />
                <YAxis stroke={CHART_TEXT} fontSize={11} tickLine={false} width={70} />
                <Tooltip
                  contentStyle={{ background: "#1b1f26", border: "1px solid #262b33", borderRadius: 6 }}
                  labelStyle={{ color: "#8b8f98" }}
                  labelFormatter={(v) => formatTime(v as string)}
                  formatter={(value) => [formatMoney(value as number), "Cumulative"]}
                />
                <Area
                  type="monotone"
                  dataKey="cumulative_profit"
                  stroke={GOLD}
                  strokeWidth={2}
                  fill="url(#profitFill)"
                />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </div>
      </Card>

      <Card>
        <CardHeader title="Wins vs Losses" subtitle="Grouped by the day a trade closed" />
        <div className="h-64 p-4 pl-0">
          {dailyQuery.isLoading ? (
            <LoadingState />
          ) : dailyQuery.isError ? (
            <ErrorState message={(dailyQuery.error as Error).message} />
          ) : !dailyQuery.data?.length ? (
            <EmptyState icon={LineChartIcon} title="No closed trades yet" />
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={dailyQuery.data} margin={{ top: 10, right: 20, left: 10, bottom: 0 }}>
                <CartesianGrid stroke="#262b33" vertical={false} />
                <XAxis dataKey="day" stroke={CHART_TEXT} fontSize={11} tickLine={false} />
                <YAxis stroke={CHART_TEXT} fontSize={11} tickLine={false} allowDecimals={false} width={30} />
                <Tooltip
                  contentStyle={{ background: "#1b1f26", border: "1px solid #262b33", borderRadius: 6 }}
                  labelStyle={{ color: "#8b8f98" }}
                />
                <Legend wrapperStyle={{ fontSize: 12, color: CHART_TEXT }} />
                <Bar dataKey="wins" name="Wins" fill={WIN} radius={[2, 2, 0, 0]} />
                <Bar dataKey="losses" name="Losses" fill={LOSS} radius={[2, 2, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </Card>
    </div>
  );
}
