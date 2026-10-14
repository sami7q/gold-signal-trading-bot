"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type {
  Analytics,
  CopyDetail,
  CopyTrade,
  EventDetail,
  LeaderEvent,
  LeaderSummary,
  LocoPositions,
  PortfolioPanel,
  AdviceAlert,
  AdviceData,
  TradeChartData,
  LogEntry,
  Mode,
  Overview,
  SettingsView,
  VirtualPanel,
} from "@/types/api";

export function useOverview(leader?: string) {
  const qs = leader ? `?leader=${encodeURIComponent(leader)}` : "";
  return useQuery({ queryKey: ["overview", leader ?? "ALL"], queryFn: () => api.get<Overview>(`/api/overview${qs}`) });
}

export function useLeaders() {
  return useQuery({ queryKey: ["leaders"], queryFn: () => api.get<LeaderSummary[]>("/api/leaders") });
}

export function useLeaderPositions(label: string | null) {
  return useQuery({
    queryKey: ["leader-positions", label],
    queryFn: () => api.get<LocoPositions>(`/api/leaders/${encodeURIComponent(label ?? "")}/positions`),
    enabled: label !== null,
  });
}

export function useLocoPositions() {
  return useQuery({ queryKey: ["loco"], queryFn: () => api.get<LocoPositions>("/api/loco") });
}

export function useCopies(status?: string, limit = 200, leader?: string) {
  const qs = new URLSearchParams({ limit: String(limit), ...(status ? { status } : {}), ...(leader ? { leader } : {}) });
  return useQuery({
    queryKey: ["copies", status ?? "ALL", limit, leader ?? "ALL"],
    queryFn: () => api.get<CopyTrade[]>(`/api/copies?${qs}`),
  });
}

export function useCopyDetail(id: number | null) {
  return useQuery({
    queryKey: ["copy", id],
    queryFn: () => api.get<CopyDetail>(`/api/copies/${id}`),
    enabled: id !== null,
  });
}

export function useEventDetail(id: number | null) {
  return useQuery({
    queryKey: ["event", id],
    queryFn: () => api.get<EventDetail>(`/api/events/${id}`),
    enabled: id !== null,
  });
}

export function useEvents(decision?: string, limit = 200, leader?: string) {
  const qs = new URLSearchParams({ limit: String(limit), ...(decision ? { decision } : {}), ...(leader ? { leader } : {}) });
  return useQuery({
    queryKey: ["events", decision ?? "ALL", limit, leader ?? "ALL"],
    queryFn: () => api.get<LeaderEvent[]>(`/api/events?${qs}`),
  });
}

export function useLogs(level?: string, limit = 300) {
  const qs = new URLSearchParams({ limit: String(limit), ...(level ? { level } : {}) });
  return useQuery({
    queryKey: ["logs", level ?? "ALL", limit],
    queryFn: () => api.get<LogEntry[]>(`/api/logs?${qs}`),
  });
}

export function useAnalytics(days: number, leader?: string) {
  const qs = new URLSearchParams({ days: String(days), ...(leader ? { leader } : {}) });
  return useQuery({
    queryKey: ["analytics", days, leader ?? "ALL"],
    queryFn: () => api.get<Analytics>(`/api/analytics?${qs}`),
    refetchInterval: 30_000,
  });
}

/** botup.md §V6: the «new trio» portfolio simulator (both scenarios, Hyperliquid prices). */
export function usePortfolio(days = 28, capital?: number) {
  return useQuery({
    queryKey: ["portfolio", days, capital ?? null],
    queryFn: () => api.get<PortfolioPanel>(`/api/portfolio?days=${days}${capital ? `&capital=${capital}` : ""}`),
    refetchInterval: 15_000,
  });
}

/** botup.md §V7: one trader + coin -- his fills, our orders, our position and its liquidation price. */
export function useTradeChart(wallet: string | null, coin: string | null, capital?: number, scenario = "real") {
  return useQuery({
    queryKey: ["trade-chart", wallet, coin, capital ?? null, scenario],
    enabled: Boolean(wallet && coin),
    queryFn: () =>
      api.get<TradeChartData>(
        `/api/portfolio/chart?wallet=${encodeURIComponent(wallet ?? "")}&coin=${encodeURIComponent(coin ?? "")}` +
          `&scenario=${scenario}${capital ? `&capital=${capital}` : ""}`,
      ),
    refetchInterval: 10_000,
  });
}

/** botup.md §V8: the AI advisor of one open trade (numbers every minute, Claude's analysis when due). */
export function useAdvice(wallet: string | null, coin: string | null) {
  return useQuery({
    queryKey: ["advice", wallet, coin],
    enabled: Boolean(wallet && coin),
    queryFn: () =>
      api.get<AdviceData>(`/api/portfolio/advice?wallet=${encodeURIComponent(wallet ?? "")}&coin=${encodeURIComponent(coin ?? "")}`),
    refetchInterval: (q) => (q.state.data?.pending ? 4_000 : 20_000),
  });
}

/** Exit alerts of every open trade in the last `hours` (badges on the open positions). */
export function useAdviceAlerts(hours = 24) {
  return useQuery({
    queryKey: ["advice-alerts", hours],
    queryFn: () => api.get<AdviceAlert[]>(`/api/portfolio/alerts?hours=${hours}`),
    refetchInterval: 30_000,
  });
}

/** Ask the bot for a new Claude analysis of this trade now. */
export function useRefreshAdvice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { wallet: string; coin: string }) => api.post<unknown>("/api/portfolio/advice/refresh", body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["advice"] }),
  });
}

/** The v5 trio panel: the virtual cross account and the observe-phase numbers. */
export function useVirtual(days = 28) {
  return useQuery({
    queryKey: ["virtual", days],
    queryFn: () => api.get<VirtualPanel>(`/api/virtual?days=${days}`),
    refetchInterval: 15_000,
  });
}

export function useSettingsView() {
  return useQuery({ queryKey: ["settings"], queryFn: () => api.get<SettingsView>("/api/settings") });
}

function useControl<TBody>(path: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: TBody) => api.post<unknown>(path, body),
    onSuccess: () => queryClient.invalidateQueries(),
  });
}

export function useSetMode() {
  return useControl<{ mode: Mode; confirm?: string; leader?: string }>("/api/control/mode");
}

/** A leader's on/off switch on the traders page. */
export function useSetLeaderActive() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ leader, active }: { leader: string; active: boolean }) =>
      api.post<unknown>(`/api/leaders/${encodeURIComponent(leader)}/active`, { active }),
    onSuccess: () => queryClient.invalidateQueries(),
  });
}

export function useKill() {
  return useControl<{ close_all: boolean }>("/api/control/kill");
}

export function useResume() {
  return useControl<Record<string, never>>("/api/control/resume");
}
