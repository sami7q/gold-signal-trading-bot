"use client";

import { useState } from "react";
import { Archive as ArchiveIcon } from "lucide-react";
import { Card, CardHeader } from "@/components/ui/card";
import { StatCard } from "@/components/ui/stat-card";
import { Select, Label } from "@/components/ui/input";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/empty-state";
import { useArchivedSignals, useArchiveSummary } from "@/hooks/use-archive";
import {
  arArchiveClassification,
  arArchiveOutcome,
  formatDateTime,
  formatMoney,
  formatPercent,
  formatPrice,
} from "@/lib/format";
import type { ArchiveClassification, ArchiveOutcome } from "@/types/api";

const CLASSIFICATION_OPTIONS: Array<{ value: ArchiveClassification | "ALL"; label: string }> = [
  { value: "ALL", label: "كل التصنيفات" },
  { value: "READY", label: "جاهزة للتنفيذ" },
  { value: "NON_EXECUTABLE_TYPE", label: "نوع غير قابل للتنفيذ" },
  { value: "NO_SL", label: "بدون وقف خسارة" },
  { value: "AMBIGUOUS_OR_OTHER", label: "غامضة/أخرى" },
];

const OUTCOME_OPTIONS: Array<{ value: ArchiveOutcome | "ALL"; label: string }> = [
  { value: "ALL", label: "كل النتائج" },
  { value: "WIN", label: "ربح" },
  { value: "LOSS", label: "خسارة" },
  { value: "UNRESOLVED", label: "لم تُحسم بعد" },
  { value: "NO_PRICE_DATA", label: "لا توجد بيانات سعر" },
];

export default function ArchivePage() {
  const [classification, setClassification] = useState<ArchiveClassification | "ALL">("ALL");
  const [outcome, setOutcome] = useState<ArchiveOutcome | "ALL">("ALL");

  const summaryQuery = useArchiveSummary();
  const signalsQuery = useArchivedSignals();
  const summary = summaryQuery.data;

  const filtered = (signalsQuery.data ?? []).filter(
    (s) =>
      (classification === "ALL" || s.classification === classification) &&
      (outcome === "ALL" || s.outcome === outcome),
  );

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-bone">الأرشيف</h1>
        <p className="mt-1 text-sm text-muted">
          تحليل استرجاعي لكل إشارة نشرتها القناة منذ بدايتها — يُبنى بأمر منفصل من المشغّل
          (<code className="font-data text-xs text-gold-dim">scripts/build_channel_archive.py</code>)،
          ولا علاقة له بصفقاتك الحقيقية أو تصفيرها. الأرباح هنا تقديرية، محسوبة من بيانات أسعار
          تاريخية (Bid) بدون احتساب فارق السعر (Spread) الدقيق.
        </p>
      </div>

      {summaryQuery.isLoading ? (
        <LoadingState />
      ) : summaryQuery.isError ? (
        <ErrorState message={(summaryQuery.error as Error).message} />
      ) : summary ? (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatCard label="إجمالي الرسائل المصنّفة" value={summary.total_signals} />
            <StatCard label="إشارات قابلة للتنفيذ" value={summary.ready_signals} tone="gold" />
            <StatCard label="نسبة الربح" value={formatPercent(summary.win_rate)} tone="gold" />
            <StatCard
              label="صافي الربح التقديري"
              value={formatMoney(summary.net_profit_estimate)}
              tone={
                Number(summary.net_profit_estimate) > 0
                  ? "win"
                  : Number(summary.net_profit_estimate) < 0
                    ? "loss"
                    : "default"
              }
            />
          </div>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatCard label="صفقات رابحة" value={summary.wins} tone="win" />
            <StatCard label="صفقات خاسرة" value={summary.losses} tone="loss" />
            <StatCard label="لم تُحسم بعد" value={summary.unresolved} />
            <StatCard label="بدون بيانات سعر" value={summary.no_price_data} />
          </div>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <StatCard label="نوع غير قابل للتنفيذ" value={summary.non_executable_signals} />
            <StatCard label="بدون وقف خسارة" value={summary.no_sl_signals} />
            <StatCard label="غامضة/أخرى" value={summary.ambiguous_or_other_signals} />
          </div>
        </>
      ) : null}

      <Card>
        <CardHeader title="كل الإشارات المؤرشفة" subtitle="من بداية القناة حتى الآن" />
        <div className="flex flex-wrap items-end gap-4 border-b border-border-soft px-4 py-3">
          <div>
            <Label>التصنيف</Label>
            <Select
              value={classification}
              onChange={(e) => setClassification(e.target.value as ArchiveClassification | "ALL")}
            >
              {CLASSIFICATION_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <Label>النتيجة</Label>
            <Select value={outcome} onChange={(e) => setOutcome(e.target.value as ArchiveOutcome | "ALL")}>
              {OUTCOME_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </Select>
          </div>
        </div>

        {signalsQuery.isLoading ? (
          <LoadingState />
        ) : signalsQuery.isError ? (
          <ErrorState message={(signalsQuery.error as Error).message} />
        ) : !filtered.length ? (
          <EmptyState
            icon={ArchiveIcon}
            title="لا توجد إشارات مؤرشفة"
            description="شغّل scripts/build_channel_archive.py من طرفية المشغّل لبناء الأرشيف."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1000px] text-left text-sm">
              <thead>
                <tr className="border-b border-border-soft text-[11px] uppercase tracking-wide text-muted-dim">
                  <th className="px-3 py-2 font-medium">تاريخ النشر</th>
                  <th className="px-3 py-2 font-medium">النص</th>
                  <th className="px-3 py-2 text-right font-medium">وقف الخسارة</th>
                  <th className="px-3 py-2 text-right font-medium">الدخول التقديري</th>
                  <th className="px-3 py-2 font-medium">التصنيف</th>
                  <th className="px-3 py-2 font-medium">النتيجة</th>
                  <th className="px-3 py-2 text-right font-medium">الربح التقديري</th>
                </tr>
              </thead>
              <tbody className="font-data">
                {filtered.map((s) => (
                  <tr key={s.id} className="border-b border-border-soft/60 last:border-0 hover:bg-white/[0.02]">
                    <td className="px-3 py-2 text-muted">{formatDateTime(s.signal_posted_at)}</td>
                    <td className="max-w-xs truncate px-3 py-2 text-bone" title={s.raw_entry_text}>
                      {s.raw_entry_text || "—"}
                    </td>
                    <td className="px-3 py-2 text-right text-loss-bright">{formatPrice(s.channel_stop_loss)}</td>
                    <td className="px-3 py-2 text-right text-bone">{formatPrice(s.simulated_entry_price)}</td>
                    <td className="px-3 py-2 text-muted">{arArchiveClassification(s.classification)}</td>
                    <td
                      className={
                        "px-3 py-2 " +
                        (s.outcome === "WIN"
                          ? "text-win-bright"
                          : s.outcome === "LOSS"
                            ? "text-loss-bright"
                            : "text-muted")
                      }
                    >
                      {s.outcome ? arArchiveOutcome(s.outcome) : "—"}
                    </td>
                    <td
                      className={
                        "px-3 py-2 text-right " +
                        (Number(s.profit_estimate ?? 0) > 0
                          ? "text-win-bright"
                          : Number(s.profit_estimate ?? 0) < 0
                            ? "text-loss-bright"
                            : "text-muted")
                      }
                    >
                      {s.profit_estimate !== null ? formatMoney(s.profit_estimate) : "—"}
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
