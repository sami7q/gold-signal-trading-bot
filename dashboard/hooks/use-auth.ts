"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";
import { clearAuthedMarker, setAuthedMarker } from "@/lib/account";
import type { LoginRequest, MeResponse } from "@/types/api";

export function useMe() {
  return useQuery({
    queryKey: ["auth", "me"],
    queryFn: () => api.get<MeResponse>("/api/auth/me"),
    // a 401 already redirects to /login; anything else (a tunnel blip) is worth another try
    retry: (failures, error) => !(error instanceof ApiError && error.status === 401) && failures < 3,
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
      clearAuthedMarker();
      queryClient.clear();
    },
  });
}
