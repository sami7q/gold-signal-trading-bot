"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { AuditEvent } from "@/types/api";

export function useLogs(limit = 200) {
  return useQuery({
    queryKey: ["logs", limit],
    queryFn: () => api.get<AuditEvent[]>(`/api/logs?limit=${limit}`),
  });
}
