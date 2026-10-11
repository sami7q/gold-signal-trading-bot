"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type {
  Analytics,
  CopyTrade,
  LeaderEvent,
  LocoPositions,
  LogEntry,
  Mode,
  Overview,
  SettingsView,
} from "@/types/api";

export function useOverview() {
  return useQuery({ queryKey: ["overview"], queryFn: () => api.get<Overview>("/api/overview") });
}

export function useLocoPositions() {
  return useQuery({ queryKey: ["loco"], queryFn: () => api.get<LocoPositions>("/api/loco") });
}

export function useCopies(status?: string, limit = 200) {
  const qs = new URLSearchParams({ limit: String(limit), ...(status ? { status } : {}) });
  return useQuery({
    queryKey: ["copies", status ?? "ALL", limit],
    queryFn: () => api.get<CopyTrade[]>(`/api/copies?${qs}`),
  });
}

export function useEvents(decision?: string, limit = 200) {
  const qs = new URLSearchParams({ limit: String(limit), ...(decision ? { decision } : {}) });
  return useQuery({
    queryKey: ["events", decision ?? "ALL", limit],
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

export function useAnalytics(days: number) {
  return useQuery({
    queryKey: ["analytics", days],
    queryFn: () => api.get<Analytics>(`/api/analytics?days=${days}`),
    refetchInterval: 30_000,
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
  return useControl<{ mode: Mode; confirm?: string }>("/api/control/mode");
}

export function useKill() {
  return useControl<{ close_all: boolean }>("/api/control/kill");
}

export function useResume() {
  return useControl<Record<string, never>>("/api/control/resume");
}
