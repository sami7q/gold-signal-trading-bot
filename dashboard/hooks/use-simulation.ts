"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { SendMessageResult, SimulationState } from "@/types/api";

export function useSimulationState() {
  return useQuery({
    queryKey: ["simulation", "state"],
    queryFn: () => api.get<SimulationState>("/api/simulation/state"),
    refetchInterval: 3_000,
  });
}

function useInvalidateAfterSimulation() {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: ["simulation"] });
    queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    queryClient.invalidateQueries({ queryKey: ["trades"] });
    queryClient.invalidateQueries({ queryKey: ["signals"] });
    queryClient.invalidateQueries({ queryKey: ["logs"] });
    queryClient.invalidateQueries({ queryKey: ["analytics"] });
  };
}

export function useSendMockMessage() {
  const invalidate = useInvalidateAfterSimulation();
  return useMutation({
    mutationFn: (payload: { text: string; channel_id?: string; message_id?: string }) =>
      api.post<SendMessageResult>("/api/simulation/messages", payload),
    onSuccess: invalidate,
  });
}

export function useSetMarketPrice() {
  const invalidate = useInvalidateAfterSimulation();
  return useMutation({
    mutationFn: (payload: { bid?: string; delta?: string }) =>
      api.post<SimulationState>("/api/simulation/market-price", payload),
    onSuccess: invalidate,
  });
}

export function useTriggerTakeProfit() {
  const invalidate = useInvalidateAfterSimulation();
  return useMutation({
    mutationFn: () => api.post<SimulationState>("/api/simulation/trigger-tp"),
    onSuccess: invalidate,
  });
}

export function useTriggerStopLoss() {
  const invalidate = useInvalidateAfterSimulation();
  return useMutation({
    mutationFn: () => api.post<SimulationState>("/api/simulation/trigger-sl"),
    onSuccess: invalidate,
  });
}

export function useResetSimulation() {
  const invalidate = useInvalidateAfterSimulation();
  return useMutation({
    mutationFn: (scope: "account" | "trades" | "all") =>
      api.post<{ reset: string }>("/api/simulation/reset", { scope }),
    onSuccess: invalidate,
  });
}
