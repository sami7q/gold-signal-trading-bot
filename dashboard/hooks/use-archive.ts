"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { ArchivedSignal, ArchiveSummary } from "@/types/api";

export function useArchiveSummary() {
  return useQuery({
    queryKey: ["archive", "summary"],
    queryFn: () => api.get<ArchiveSummary>("/api/archive/summary"),
  });
}

export function useArchivedSignals(limit = 500) {
  return useQuery({
    queryKey: ["archive", "signals", limit],
    queryFn: () => api.get<ArchivedSignal[]>(`/api/archive?limit=${limit}`),
  });
}
