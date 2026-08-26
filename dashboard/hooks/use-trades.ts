"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Trade, TradeResult, TradeStatus } from "@/types/api";

export interface TradeFilters {
  status?: TradeStatus;
  result?: TradeResult;
  dateFrom?: string;
  dateTo?: string;
}

export function useTrades(filters: TradeFilters = {}) {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.result) params.set("result", filters.result);
  if (filters.dateFrom) params.set("date_from", filters.dateFrom);
  if (filters.dateTo) params.set("date_to", filters.dateTo);
  const qs = params.toString();

  return useQuery({
    queryKey: ["trades", filters],
    queryFn: () => api.get<Trade[]>(`/api/trades${qs ? `?${qs}` : ""}`),
  });
}
