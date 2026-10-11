"use client";

import { useState } from "react";
import { ScrollText } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Label, Select } from "@/components/ui/input";
import { SeverityBadge } from "@/components/ui/badge";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/empty-state";
import { useLogs } from "@/hooks/use-loco";
import { formatDateTime, payloadSummary } from "@/lib/format";

export default function LogsPage() {
  const [level, setLevel] = useState("");
  const query = useLogs(level || undefined, 400);

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-5">
      <div>
        <h1 className="text-xl font-semibold text-bone">السجلات</h1>
        <p className="mt-1 text-sm text-muted">كل قرار، وكل انقطاع واتصال، وكل تغيير في الوضع أو الإيقاف — من البوت مباشرة.</p>
      </div>

      <Card>
        <div className="flex items-end gap-4 border-b border-border-soft px-4 py-3">
          <div>
            <Label>المستوى</Label>
            <Select value={level} onChange={(e) => setLevel(e.target.value)}>
              <option value="">الكل</option>
              <option value="WARNING">التحذيرات والأخطاء فقط</option>
            </Select>
          </div>
        </div>

        {query.isLoading ? (
          <LoadingState />
        ) : query.isError ? (
          <ErrorState message={(query.error as Error).message} />
        ) : !query.data?.length ? (
          <EmptyState icon={ScrollText} title="لا توجد سجلات" />
        ) : (
          <ul className="divide-y divide-border-soft/60 font-data text-sm">
            {query.data.map((entry) => (
              <li key={entry.id} className="flex flex-col gap-1.5 px-4 py-2.5 sm:flex-row sm:items-start sm:gap-3">
                <div className="flex flex-wrap items-center gap-2 sm:contents">
                  <span className="shrink-0 text-muted-dim">{formatDateTime(entry.ts)}</span>
                  <SeverityBadge severity={entry.level} />
                  <span className="shrink-0 text-gold-dim">{entry.type}</span>
                </div>
                <span className="min-w-0 flex-1 break-all text-xs text-bone">{payloadSummary(entry.payload)}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
