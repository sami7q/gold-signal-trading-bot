"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { AuditEvent, DashboardSummaryResponse, HealthStatus, SignalSourceHealth, Trade } from "@/types/api";

export function useDashboardSummary() {
  return useQuery({
    queryKey: ["dashboard", "summary"],
    queryFn: () => api.get<DashboardSummaryResponse>("/api/dashboard/summary"),
  });
}

export function useRecentTrades(limit = 8) {
  return useQuery({
    queryKey: ["dashboard", "recent-trades", limit],
    queryFn: () => api.get<Trade[]>(`/api/dashboard/recent-trades?limit=${limit}`),
  });
}

export function useRecentEvents(limit = 10) {
  return useQuery({
    queryKey: ["dashboard", "recent-events", limit],
    queryFn: () => api.get<AuditEvent[]>(`/api/dashboard/recent-events?limit=${limit}`),
  });
}

export function useBrokerHealth() {
  return useQuery({
    queryKey: ["health", "broker"],
    queryFn: () => api.get<HealthStatus & { broker: string }>("/api/health/broker"),
  });
}

export function useSignalSourceHealth() {
  return useQuery({
    queryKey: ["health", "signal-source"],
    queryFn: () => api.get<SignalSourceHealth>("/api/health/signal-source"),
  });
}
