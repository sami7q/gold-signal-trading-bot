"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { clearActiveAccountId, clearAuthedMarker, setAuthedMarker } from "@/lib/account";
import type { LoginRequest, MeResponse } from "@/types/api";

export function useMe() {
  return useQuery({
    queryKey: ["auth", "me"],
    queryFn: () => api.get<MeResponse>("/api/auth/me"),
    // A 401 here is an expected, normal state (not logged in yet) -- not
    // worth retrying, and callers read `isError`/`data` themselves rather
    // than needing this to throw anywhere.
    retry: false,
    refetchInterval: false,
  });
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: LoginRequest) => api.post<MeResponse>("/api/auth/login", payload),
    onSuccess: (data) => {
      setAuthedMarker();
      queryClient.setQueryData(["auth", "me"], data);
    },
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<void>("/api/auth/logout"),
    onSuccess: () => {
      clearActiveAccountId();
      clearAuthedMarker();
      queryClient.clear();
    },
  });
}
