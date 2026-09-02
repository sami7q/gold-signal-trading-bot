"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { MT5Account, MT5AccountCreate, MT5AccountUpdate } from "@/types/api";

export function useAccounts() {
  return useQuery({
    queryKey: ["accounts"],
    queryFn: () => api.get<MT5Account[]>("/api/accounts"),
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

export function useSetLiveAccount() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, confirmPhrase }: { id: number; confirmPhrase: string }) =>
      api.post<MT5Account>(`/api/accounts/${id}/set-live`, { confirm_phrase: confirmPhrase }),
    onSuccess: () => invalidateAccounts(queryClient),
  });
}
