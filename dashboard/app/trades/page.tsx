"use client";

import { Fragment, useState } from "react";
import { ChevronDown, Inbox } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Label, Select } from "@/components/ui/input";
import { CopyStatusBadge, LeaderBadge, SideBadge } from "@/components/ui/badge";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/empty-state";
import { LeaderSelect } from "@/components/trade/leader-select";
import { TradeDetail } from "@/components/trade/trade-detail";
import { useCopies } from "@/hooks/use-loco";
import { cn, formatDateTime, formatMoney, formatMs, formatNumber, formatPrice, pnlClass } from "@/lib/format";
import { urlParam } from "@/lib/url";

const FILTERS = [
  { value: "", label: "الكل" },
  { value: "OPEN", label: "مفتوحة" },
  { value: "CLOSED", label: "مغلقة" },
  { value: "CLOSED_TP", label: "هدف الربح" },
  { value: "CLOSED_LIQ", label: "تصفية" },
  { value: "OBSERVED_ONLY", label: "مراقبة فقط" },
  { value: "FAILED", label: "فشلت" },
];

const COLUMNS = 16;

/** `/trades?open=12` (the link from a leader's move) opens that trade's details straight away. */
function openFromUrl(): number | null {
  const id = Number(urlParam("open"));
  return Number.isInteger(id) && id > 0 ? id : null;
}

export default function TradesPage() {
  const [status, setStatus] = useState("");
  const [openId, setOpenId] = useState<number | null>(openFromUrl);
  const [leader, setLeader] = useState(() => urlParam("leader") ?? "");
  const query = useCopies(status || undefined, 500, leader || undefined);
  const toggle = (id: number) => setOpenId((cur) => (cur === id ? null : id));

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-5">
      <div>
        <h1 className="text-xl font-semibold text-bone">صفقاتنا</h1>
        <p className="mt-1 text-sm text-muted">
          كل صفقة نسخها البوت من المتداولين. اضغط على أي صفقة لترى تفاصيلها كاملة: سعر المتداول مقابل سعرنا، الرسوم والصافي،
          وخط سيرها خطوة بخطوة. صفقات &quot;مراقبة فقط&quot; محسوبة افتراضيًا ولم تُرسل للوسيط.
        </p>
      </div>

      <Card>
        <div className="flex flex-wrap items-end gap-4 border-b border-border-soft px-4 py-3">
          <div>
            <Label>الحالة</Label>
            <Select value={status} onChange={(e) => setStatus(e.target.value)}>
              {FILTERS.map((f) => (
                <option key={f.value} value={f.value}>{f.label}</option>
              ))}
            </Select>
          </div>
          <LeaderSelect value={leader} onChange={setLeader} />
          {query.data ? <p className="pb-1.5 text-xs text-muted-dim">{query.data.length} صفقة</p> : null}
        </div>

        {query.isLoading ? (
          <LoadingState />
        ) : query.isError ? (
          <ErrorState message={(query.error as Error).message} />
        ) : !query.data?.length ? (
          <EmptyState icon={Inbox} title="لا توجد صفقات تطابق هذا الفلتر" />
        ) : (
          <div className="@container overflow-x-auto">
            <table className="w-full min-w-[1200px] text-left text-sm">
              <thead>
                <tr className="border-b border-border-soft text-[11px] text-muted-dim">
                  <th className="w-8 px-2 py-2" aria-label="التفاصيل" />
                  <th className="px-3 py-2 font-medium">#</th>
                  <th className="px-3 py-2 font-medium">المتداول</th>
                  <th className="px-3 py-2 font-medium">الفتح</th>
                  <th className="px-3 py-2 font-medium">الرمز</th>
                  <th className="px-3 py-2 font-medium">الاتجاه</th>
                  <th className="px-3 py-2 font-medium">الحالة</th>
                  <th className="px-3 py-2 text-right font-medium">الصافي</th>
                  <th className="px-3 py-2 text-right font-medium">الكمية</th>
                  <th className="px-3 py-2 text-right font-medium">سعر المتداول</th>
                  <th className="px-3 py-2 text-right font-medium">سعرنا</th>
                  <th className="px-3 py-2 text-right font-medium">الانزلاق</th>
                  <th className="px-3 py-2 text-right font-medium">التنفيذ</th>
                  <th className="px-3 py-2 text-right font-medium">الهدف / السقف</th>
                  <th className="px-3 py-2 text-right font-medium">الخروج</th>
                  <th className="px-3 py-2 font-medium">الإغلاق</th>
                </tr>
              </thead>
              <tbody className="font-data whitespace-nowrap">
                {query.data.map((c) => {
                  const open = openId === c.id;
                  return (
                    <Fragment key={c.id}>
                      <tr
                        onClick={() => toggle(c.id)}
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
                            onClick={(e) => { e.stopPropagation(); toggle(c.id); }}
                            className="flex h-6 w-6 items-center justify-center rounded text-muted hover:text-gold-bright focus-visible:outline focus-visible:outline-1 focus-visible:outline-gold"
                          >
                            <ChevronDown className={cn("h-4 w-4 transition-transform", open && "rotate-180 text-gold-bright")} />
                          </button>
                        </td>
                        <td className="px-3 py-2 text-muted">#{c.id}</td>
                        <td className="px-3 py-2 font-sans"><LeaderBadge leader={c.leader} /></td>
                        <td className="px-3 py-2 text-muted">{formatDateTime(c.opened_at)}</td>
                        <td className="px-3 py-2 text-bone">{c.symbol}</td>
                        <td className="px-3 py-2"><SideBadge side={c.side} /></td>
                        <td className="px-3 py-2"><CopyStatusBadge status={c.status} /></td>
                        <td className={`px-3 py-2 text-right ${pnlClass(c.realized_pnl)}`}>{c.closed_at ? formatMoney(c.realized_pnl) : "—"}</td>
                        <td className="px-3 py-2 text-right text-bone">{formatNumber(c.qty_initial, 6)}</td>
                        <td className="px-3 py-2 text-right text-muted">{formatPrice(c.leader_entry_px)}</td>
                        <td className="px-3 py-2 text-right text-bone">{formatPrice(c.entry_price)}</td>
                        <td className="px-3 py-2 text-right text-muted">{c.slippage_bps !== null ? `${formatNumber(c.slippage_bps, 1)} bps` : "—"}</td>
                        <td className="px-3 py-2 text-right text-muted">{formatMs(c.latency_order_ms)}</td>
                        <td className="px-3 py-2 text-right">
                          {c.strategy === "cap20_cross" ? (
                            <span className={c.capped ? "text-warn" : "text-muted"} title="المستخدم من سقف الهامش (20% من الرصيد)">
                              {c.cap_used_pct !== null ? `${formatNumber(c.cap_used_pct, 0)}%` : "—"}
                              {c.adds_done ? ` · +${c.adds_done}` : ""}
                            </span>
                          ) : (
                            <span className="text-win-bright">{formatPrice(c.tp_price)}</span>
                          )}
                        </td>
                        <td className="px-3 py-2 text-right text-bone">{formatPrice(c.exit_price_avg)}</td>
                        <td className="px-3 py-2 text-muted">{formatDateTime(c.closed_at)}</td>
                      </tr>
                      {open ? (
                        <tr className="border-b border-border-soft">
                          <td colSpan={COLUMNS} className="whitespace-normal p-0">
                            <TradeDetail id={c.id} />
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
