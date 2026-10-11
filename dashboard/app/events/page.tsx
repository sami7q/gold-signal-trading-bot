"use client";

import { useState } from "react";
import { Activity } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Label, Select } from "@/components/ui/input";
import { DecisionBadge, EventTypeBadge, SideBadge } from "@/components/ui/badge";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/empty-state";
import { useEvents } from "@/hooks/use-loco";
import { arRejection, formatDateTime, formatMs, formatNumber, formatPrice } from "@/lib/format";

const FILTERS = [
  { value: "", label: "كل القرارات" },
  { value: "COPIED", label: "نُسخت" },
  { value: "FOLLOWED", label: "تمت المتابعة (تقليص/إغلاق)" },
  { value: "OBSERVED_ONLY", label: "مراقبة فقط" },
  { value: "REJECTED", label: "مرفوضة" },
  { value: "IGNORED_ADD", label: "تعزيز — متجاهل" },
  { value: "NO_MATCHING_COPY", label: "لا صفقة مقابلة" },
  { value: "FAILED", label: "فشل" },
];

export default function EventsPage() {
  const [decision, setDecision] = useState("");
  const query = useEvents(decision || undefined, 500);

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-5">
      <div>
        <h1 className="text-xl font-semibold text-bone">حركات Loco</h1>
        <p className="mt-1 text-sm text-muted">
          كل حركة يقوم بها Loco (فتح، تعزيز، تقليص، إغلاق، انعكاس) وقرار البوت فيها مع السبب. التعزيز يُسجَّل ولا
          يُنسخ أبدًا، والإغلاقات لا تُمنع أبدًا.
        </p>
      </div>

      <Card>
        <div className="flex flex-wrap items-end gap-4 border-b border-border-soft px-4 py-3">
          <div>
            <Label>القرار</Label>
            <Select value={decision} onChange={(e) => setDecision(e.target.value)}>
              {FILTERS.map((f) => (
                <option key={f.value} value={f.value}>{f.label}</option>
              ))}
            </Select>
          </div>
        </div>

        {query.isLoading ? (
          <LoadingState />
        ) : query.isError ? (
          <ErrorState message={(query.error as Error).message} />
        ) : !query.data?.length ? (
          <EmptyState icon={Activity} title="لا توجد حركات بعد" description="Loco يفتح تقريبًا صفقة إلى صفقتين يوميًا." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1100px] text-left text-sm">
              <thead>
                <tr className="border-b border-border-soft text-[11px] text-muted-dim">
                  <th className="px-3 py-2 font-medium">الوقت</th>
                  <th className="px-3 py-2 font-medium">العملة</th>
                  <th className="px-3 py-2 font-medium">الحركة</th>
                  <th className="px-3 py-2 font-medium">الاتجاه</th>
                  <th className="px-3 py-2 text-right font-medium">سعر Loco</th>
                  <th className="px-3 py-2 text-right font-medium">النسبة</th>
                  <th className="px-3 py-2 text-right font-medium">زمن الالتقاط</th>
                  <th className="px-3 py-2 font-medium">القرار</th>
                  <th className="px-3 py-2 font-medium">السبب</th>
                </tr>
              </thead>
              <tbody className="font-data whitespace-nowrap">
                {query.data.map((e) => (
                  <tr key={e.id} className="border-b border-border-soft/60 last:border-0 hover:bg-white/[0.02]">
                    <td className="px-3 py-2 text-muted">{formatDateTime(e.detected_at)}</td>
                    <td className="px-3 py-2 text-bone">{e.coin}</td>
                    <td className="px-3 py-2"><EventTypeBadge type={e.type} /></td>
                    <td className="px-3 py-2"><SideBadge side={e.side} /></td>
                    <td className="px-3 py-2 text-right text-bone">{formatPrice(e.leader_px)}</td>
                    <td className="px-3 py-2 text-right text-muted">{e.fraction ? `${formatNumber(Number(e.fraction) * 100, 1)}%` : "—"}</td>
                    <td className="px-3 py-2 text-right text-muted">{formatMs(e.lag_ms)}</td>
                    <td className="px-3 py-2"><DecisionBadge decision={e.decision} /></td>
                    <td dir="auto" className="max-w-[22rem] truncate px-3 py-2 text-start font-sans text-xs text-warn" title={e.detail ?? undefined}>
                      {e.rejection ? arRejection(e.rejection) : <span className="text-muted-dim">{e.detail ?? "—"}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
