"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { PipPreview, TradingSettings, TradingSettingsUpdate } from "@/types/api";

export function useSettings() {
  return useQuery({
    queryKey: ["settings"],
    queryFn: () => api.get<TradingSettings>("/api/settings"),
  });
}

/**
 * Read-only preview of what a pip target actually means in price terms.
 * Always calls the backend (`app.trading.pips`, the same module the
 * Trading Engine itself uses) — this hook must never compute pip math
 * itself, only display what the backend returns.
 */
export function usePipPreview(targetPips: string) {
  return useQuery({
    queryKey: ["settings", "pip-preview", targetPips],
    queryFn: () =>
      api.get<PipPreview>(`/api/settings/pip-preview?target_pips=${encodeURIComponent(targetPips)}`),
    enabled: Number(targetPips) > 0,
  });
}

export function useUpdateSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (patch: TradingSettingsUpdate) => api.patch<TradingSettings>("/api/settings", patch),
    onSuccess: (data) => {
      queryClient.setQueryData(["settings"], data);
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}
