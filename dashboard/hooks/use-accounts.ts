"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { MT5Account, MT5AccountCreate, MT5AccountUpdate } from "@/types/api";

export function useAccounts() {
  return useQuery({
    queryKey: ["accounts"],
    queryFn: () => api.get<MT5Account[]>("/api/accounts"),
    // `is_live` is a live heartbeat signal now (concurrent-execution
    // "Phase 2" update), not a manually-toggled flag -- refetch on a timer
    // so the "متصل الآن"/"غير متصل" badges update on their own instead of
    // only ever reflecting the moment this page was loaded.
    refetchInterval: 20_000,
  });
}

function invalidateAccounts(queryClient: ReturnType<typeof useQueryClient>) {
  queryClient.invalidateQueries({ queryKey: ["accounts"] });
  queryClient.invalidateQueries({ queryKey: ["auth", "me"] });
}

export function useCreateAccount() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: MT5AccountCreate) => api.post<MT5Account>("/api/accounts", payload),
    onSuccess: () => invalidateAccounts(queryClient),
  });
}

export function useUpdateAccount() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: MT5AccountUpdate }) =>
      api.patch<MT5Account>(`/api/accounts/${id}`, patch),
    onSuccess: () => invalidateAccounts(queryClient),
  });
}

export function useArchiveAccount() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.patch<MT5Account>(`/api/accounts/${id}/archive`),
    onSuccess: () => invalidateAccounts(queryClient),
  });
}
