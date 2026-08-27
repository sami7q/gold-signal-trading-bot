"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { DailyPerformance, OrderPositionPerformance, ProfitCurvePoint } from "@/types/api";

export function useProfitCurve() {
  return useQuery({
    queryKey: ["analytics", "profit-curve"],
    queryFn: () => api.get<ProfitCurvePoint[]>("/api/dashboard/analytics/profit-curve"),
  });
}

export function useDailyPerformance() {
  return useQuery({
    queryKey: ["analytics", "daily-performance"],
    queryFn: () => api.get<DailyPerformance[]>("/api/dashboard/analytics/daily-performance"),
  });
}

export function useTradeOrderPerformance() {
  return useQuery({
    queryKey: ["analytics", "trade-order-performance"],
    queryFn: () =>
      api.get<OrderPositionPerformance[]>("/api/dashboard/analytics/trade-order-performance"),
  });
}
