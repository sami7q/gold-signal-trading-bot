"use client";

import { useState } from "react";
import { ScrollText } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Select, Label } from "@/components/ui/input";
import { SeverityBadge } from "@/components/ui/badge";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/empty-state";
import { useLogs } from "@/hooks/use-logs";
import { arSeverity, formatDateTime } from "@/lib/format";
import type { AuditSeverity } from "@/types/api";

const SEVERITIES: Array<AuditSeverity | "ALL"> = ["ALL", "INFO", "WARNING", "ERROR", "CRITICAL"];

export default function LogsPage() {
  const [severity, setSeverity] = useState<AuditSeverity | "ALL">("ALL");
  const logsQuery = useLogs(300);

  const events = (logsQuery.data ?? []).filter((e) => severity === "ALL" || e.severity === severity);

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-5">
      <div>
        <h1 className="text-xl font-semibold text-bone">السجلات</h1>
        <p className="mt-1 text-sm text-muted">سجل التدقيق الكامل — كل إشارة وصفقة وتغيير إعدادات.</p>
      </div>

      <Card>
        <div className="flex items-end gap-4 border-b border-border-soft px-4 py-3">
          <div>
            <Label>الخطورة</Label>
            <Select value={severity} onChange={(e) => setSeverity(e.target.value as AuditSeverity | "ALL")}>
              {SEVERITIES.map((s) => (
                <option key={s} value={s}>
                  {s === "ALL" ? "الكل" : arSeverity(s)}
                </option>
              ))}
            </Select>
          </div>
        </div>

        {logsQuery.isLoading ? (
          <LoadingState />
        ) : logsQuery.isError ? (
          <ErrorState message={(logsQuery.error as Error).message} />
        ) : !events.length ? (
          <EmptyState icon={ScrollText} title="لا توجد أحداث" />
        ) : (
          <ul className="divide-y divide-border-soft/60 font-data text-sm">
            {events.map((event) => (
              <li
                key={event.id}
                className="flex flex-col gap-1.5 px-4 py-2.5 sm:flex-row sm:items-start sm:gap-3"
              >
                <div className="flex flex-wrap items-center gap-2 sm:contents">
                  <span className="shrink-0 text-muted-dim">{formatDateTime(event.created_at)}</span>
                  <SeverityBadge severity={event.severity} />
                  <span className="shrink-0 text-gold-dim">{event.event_type}</span>
                </div>
                <span className="min-w-0 flex-1 text-bone">{event.message}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
