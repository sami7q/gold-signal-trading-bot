"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Signal, SignalStatus } from "@/types/api";

export function useSignals(status?: SignalStatus) {
  const qs = status ? `?status_filter=${status}` : "";
  return useQuery({
    queryKey: ["signals", status ?? "all"],
    queryFn: () => api.get<Signal[]>(`/api/signals${qs}`),
  });
}
