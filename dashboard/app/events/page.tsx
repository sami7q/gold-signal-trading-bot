"use client";

import { Fragment, useState } from "react";
import { Activity, ChevronDown } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Label, Select } from "@/components/ui/input";
import { DecisionBadge, EventTypeBadge, LeaderBadge, SideBadge } from "@/components/ui/badge";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/empty-state";
import { EventDetail } from "@/components/trade/event-detail";
import { LeaderSelect } from "@/components/trade/leader-select";
import { useEvents } from "@/hooks/use-loco";
import { arRejection, cn, formatDateTime, formatMs, formatNumber, formatPrice } from "@/lib/format";
import { urlParam } from "@/lib/url";

const FILTERS = [
  { value: "", label: "كل القرارات" },
  { value: "COPIED", label: "نُسخت" },
  { value: "FOLLOWED", label: "تمت المتابعة (تقليص/إغلاق)" },
  { value: "REDUCED", label: "قُلّصت مع المتداول" },
  { value: "CLOSED", label: "أُغلقت مع المتداول" },
  { value: "OBSERVED_ONLY", label: "مراقبة فقط" },
  { value: "REJECTED", label: "مرفوضة" },
  { value: "ADDED", label: "تعزيز منسوخ" },
  { value: "ADD_PENDING", label: "تعزيز مؤجَّل" },
  { value: "ADD_CAPPED", label: "بلغت السقف" },
  { value: "IGNORED_ADD", label: "تعزيز — متجاهل" },
  { value: "NO_MATCHING_COPY", label: "لا صفقة مقابلة" },
  { value: "FAILED", label: "فشل" },
];

const COLUMNS = 14;

export default function EventsPage() {
  const [decision, setDecision] = useState("");
  const [openId, setOpenId] = useState<number | null>(null);
  const [leader, setLeader] = useState(() => urlParam("leader") ?? "");
  const query = useEvents(decision || undefined, 500, leader || undefined);
  const toggle = (id: number) => setOpenId((cur) => (cur === id ? null : id));

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-5">
      <div>
        <h1 className="text-xl font-semibold text-bone">حركات المتداولين</h1>
        <p className="mt-1 text-sm text-muted">
          كل حركة يقوم بها أي متداول ننسخه (فتح، تعزيز، تقليص، إغلاق، انعكاس) وقرار البوت فيها مع السبب. اضغط على أي
          حركة لترى أمره كاملًا، مركزه قبل وبعد، وصفقتنا المقابلة. التعزيز يُسجَّل ولا يُنسخ، والإغلاقات لا تُمنع أبدًا.
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
          <LeaderSelect value={leader} onChange={setLeader} />
        </div>

        {query.isLoading ? (
          <LoadingState />
        ) : query.isError ? (
          <ErrorState message={(query.error as Error).message} />
        ) : !query.data?.length ? (
          <EmptyState icon={Activity} title="لا توجد حركات بعد" description="كل حركة يقوم بها أي متداول تظهر هنا خلال ثوانٍ." />
        ) : (
          <div className="@container overflow-x-auto">
            <table className="w-full min-w-[1100px] text-left text-sm">
              <thead>
                <tr className="border-b border-border-soft text-[11px] text-muted-dim">
                  <th className="w-8 px-2 py-2" aria-label="التفاصيل" />
                  <th className="px-3 py-2 font-medium">الوقت</th>
                  <th className="px-3 py-2 font-medium">المتداول</th>
                  <th className="px-3 py-2 font-medium">العملة</th>
                  <th className="px-3 py-2 font-medium">الحركة</th>
                  <th className="px-3 py-2 font-medium">الاتجاه</th>
                  <th className="px-3 py-2 text-right font-medium">سعره</th>
                  <th className="px-3 py-2 text-right font-medium">سعرنا</th>
                  <th className="px-3 py-2 text-right font-medium">الفرق</th>
                  <th className="px-3 py-2 text-right font-medium">مركزه بعدها</th>
                  <th className="px-3 py-2 text-right font-medium">زمن الالتقاط</th>
                  <th className="px-3 py-2 font-medium">القرار</th>
                  <th className="px-3 py-2 font-medium">صفقتنا</th>
                  <th className="px-3 py-2 font-medium">السبب</th>
                </tr>
              </thead>
              <tbody className="font-data whitespace-nowrap">
                {query.data.map((e) => {
                  const open = openId === e.id;
                  return (
                    <Fragment key={e.id}>
                      <tr
                        onClick={() => toggle(e.id)}
                        className={cn(
                          "cursor-pointer border-b border-border-soft/60 hover:bg-white/[0.03]",
                          open && "bg-gold/[0.06] hover:bg-gold/[0.08]",
                        )}
                      >
                        <td className="px-2 py-2">
                          <button
                            type="button"
                            aria-expanded={open}
                            aria-label={open ? "إخفاء التفاصيل" : "عرض التفاصيل"}
                            onClick={(ev) => { ev.stopPropagation(); toggle(e.id); }}
                            className="flex h-6 w-6 items-center justify-center rounded text-muted hover:text-gold-bright focus-visible:outline focus-visible:outline-1 focus-visible:outline-gold"
                          >
                            <ChevronDown className={cn("h-4 w-4 transition-transform", open && "rotate-180 text-gold-bright")} />
                          </button>
                        </td>
                        <td className="px-3 py-2 text-muted">{formatDateTime(e.detected_at)}</td>
                        <td className="px-3 py-2 font-sans"><LeaderBadge leader={e.leader} /></td>
                        <td className="px-3 py-2 text-bone">{e.coin}</td>
                        <td className="px-3 py-2"><EventTypeBadge type={e.type} /></td>
                        <td className="px-3 py-2"><SideBadge side={e.side} /></td>
                        <td className="px-3 py-2 text-right text-bone">{formatPrice(e.leader_px)}</td>
                        <td className="px-3 py-2 text-right text-bone">{formatPrice(e.our_px)}</td>
                        <td className="px-3 py-2 text-right text-muted">{e.slippage_bps !== null ? `${formatNumber(e.slippage_bps, 1)} bps` : "—"}</td>
                        <td className="px-3 py-2 text-right text-muted">
                          {e.pos_after !== null ? formatNumber(e.pos_after, 6) : e.fraction ? `${formatNumber(Number(e.fraction) * 100, 1)}%` : "—"}
                        </td>
                        <td className="px-3 py-2 text-right text-muted">{formatMs(e.lag_ms)}</td>
                        <td className="px-3 py-2"><DecisionBadge decision={e.decision} /></td>
                        <td className="px-3 py-2 text-muted">{e.copy_id ? `#${e.copy_id}` : "—"}</td>
                        <td dir="auto" className="max-w-[22rem] truncate px-3 py-2 text-start font-sans text-xs text-warn" title={e.detail ?? undefined}>
                          {e.rejection ? arRejection(e.rejection) : <span className="text-muted-dim">{e.detail ?? "—"}</span>}
                        </td>
                      </tr>
                      {open ? (
                        <tr className="border-b border-border-soft">
                          <td colSpan={COLUMNS} className="whitespace-normal p-0">
                            <EventDetail id={e.id} />
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
