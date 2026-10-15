"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, isNotFound } from "@/lib/api";
import { livePoll } from "@/lib/live";
import type {
  Analytics,
  CopyDetail,
  CopyTrade,
  EventDetail,
  LeaderEvent,
  LeaderSummary,
  LocoPositions,
  PortfolioCompare,
  PortfolioPanel,
  ResearchData,
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
    placeholderData: keepPreviousData,
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
    placeholderData: keepPreviousData,
  });
}

export function useLogs(level?: string, limit = 300) {
  const qs = new URLSearchParams({ limit: String(limit), ...(level ? { level } : {}) });
  return useQuery({
    queryKey: ["logs", level ?? "ALL", limit],
    queryFn: () => api.get<LogEntry[]>(`/api/logs?${qs}`),
    placeholderData: keepPreviousData,
  });
}

export function useAnalytics(days: number, leader?: string) {
  const qs = new URLSearchParams({ days: String(days), ...(leader ? { leader } : {}) });
  return useQuery({
    queryKey: ["analytics", days, leader ?? "ALL"],
    queryFn: () => api.get<Analytics>(`/api/analytics?${qs}`),
    placeholderData: keepPreviousData,
    refetchInterval: livePoll(30_000),
  });
}

/** Query options shared by the hooks and the sidebar's hover prefetch (botup.md §V12): same key, same fetch. */
export const portfolioOptions = (days = 28, capital?: number, group = "trio", leverage?: number) => ({
  queryKey: ["portfolio", group, days, capital ?? null, leverage ?? null] as const,
  queryFn: () =>
    api.get<PortfolioPanel>(
      `/api/portfolio?days=${days}&group=${encodeURIComponent(group)}${capital ? `&capital=${capital}` : ""}` +
        (leverage ? `&leverage=${leverage}` : ""),
    ),
});

export const compareOptions = (capital?: number, leverage?: number) => ({
  queryKey: ["portfolio-compare", capital ?? null, leverage ?? null] as const,
  queryFn: () => {
    const qs = new URLSearchParams({ ...(capital ? { capital: String(capital) } : {}), ...(leverage ? { leverage: String(leverage) } : {}) });
    return api.get<PortfolioCompare>(`/api/portfolio/compare${qs.size ? `?${qs}` : ""}`);
  },
});

export const researchOptions = () => ({
  queryKey: ["research"] as const,
  queryFn: () => api.get<ResearchData>("/api/research"),
});

/** botup.md §V6: the «new trio» portfolio simulator (both scenarios, Hyperliquid prices); §V10 any section. */
export function usePortfolio(days = 28, capital?: number, group = "trio", leverage?: number) {
  return useQuery({
    ...portfolioOptions(days, capital, group, leverage),
    refetchInterval: livePoll(15_000),
    placeholderData: keepPreviousData, // switching balance / period keeps the page on screen while it loads
  });
}

/** botup.md §V10: every simulator section side by side. */
export function usePortfolioCompare(capital?: number, leverage?: number) {
  return useQuery({ ...compareOptions(capital, leverage), refetchInterval: livePoll(30_000), placeholderData: keepPreviousData });
}

/** botup.md §V7: one trader + coin -- his fills, our orders, our position and its liquidation price. */
export function useTradeChart(
  wallet: string | null,
  coin: string | null,
  capital?: number,
  scenario = "real",
  group = "trio",
  leverage?: number,
) {
  return useQuery({
    queryKey: ["trade-chart", group, wallet, coin, capital ?? null, scenario, leverage ?? null],
    enabled: Boolean(wallet && coin),
    queryFn: () =>
      api.get<TradeChartData>(
        `/api/portfolio/chart?wallet=${encodeURIComponent(wallet ?? "")}&coin=${encodeURIComponent(coin ?? "")}` +
          `&scenario=${scenario}&group=${encodeURIComponent(group)}${capital ? `&capital=${capital}` : ""}` +
          (leverage ? `&leverage=${leverage}` : ""),
      ),
    refetchInterval: livePoll(10_000),
  });
}

/** botup.md §V8: the AI advisor of one open trade (numbers every minute, Claude's analysis when due). */
export function useAdvice(wallet: string | null, coin: string | null) {
  return useQuery({
    queryKey: ["advice", wallet, coin],
    enabled: Boolean(wallet && coin),
    queryFn: () =>
      api.get<AdviceData>(`/api/portfolio/advice?wallet=${encodeURIComponent(wallet ?? "")}&coin=${encodeURIComponent(coin ?? "")}`),
    refetchInterval: (q) => (q.state.data?.pending ? 4_000 : livePoll(20_000)()),
  });
}

/** Exit alerts of every open trade in the last `hours` (badges on the open positions). */
export function useAdviceAlerts(hours = 24) {
  return useQuery({
    queryKey: ["advice-alerts", hours],
    queryFn: () => api.get<AdviceAlert[]>(`/api/portfolio/alerts?hours=${hours}`),
    refetchInterval: livePoll(30_000),
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

/** botup.md §V9: the research export (top 10 wallets, mixes, reports). */
export function useResearch() {
  return useQuery({ ...researchOptions(), staleTime: 60_000, retry: (n, e) => !isNotFound(e) && n < 1 });
}

/** The v5 trio panel: the virtual cross account and the observe-phase numbers. */
export function useVirtual(days = 28) {
  return useQuery({
    queryKey: ["virtual", days],
    queryFn: () => api.get<VirtualPanel>(`/api/virtual?days=${days}`),
    refetchInterval: livePoll(15_000),
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
