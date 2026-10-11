"use client";

import Link from "next/link";
import { Users } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge, ModeBadge } from "@/components/ui/badge";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/empty-state";
import { ModePicker } from "@/components/trade/mode-picker";
import { useLeaders, useOverview, useSetLeaderActive } from "@/hooks/use-loco";
import { ApiError } from "@/lib/api";
import {
  brokerLabel,
  cn,
  formatMoney,
  formatNumber,
  formatRelativeTime,
  formatUsd,
  leaderName,
  pnlClass,
} from "@/lib/format";
import type { LeaderSummary } from "@/types/api";

/** The leader's on/off switch. Off = no new copies of him; whatever is open still follows his exits. */
function LeaderSwitch({ leader }: { leader: LeaderSummary }) {
  const setActive = useSetLeaderActive();
  const on = !leader.paused;
  const error = setActive.error ? (setActive.error instanceof ApiError ? setActive.error.message : "تعذّر التغيير.") : null;
  return (
    <div className="flex flex-col items-end gap-1">
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-label={on ? `إيقاف نسخ ${leaderName(leader.label)}` : `تفعيل نسخ ${leaderName(leader.label)}`}
        disabled={setActive.isPending}
        onClick={() => setActive.mutate({ leader: leader.label, active: !on })}
        className={cn(
          "flex items-center gap-2.5 rounded-full border py-1 pe-1 ps-3 text-sm font-medium transition-colors disabled:opacity-60",
          "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-gold",
          on ? "border-win/50 bg-win/10 text-win-bright" : "border-loss/50 bg-loss/10 text-loss-bright",
        )}
      >
        {on ? "مفعّل" : "موقوف"}
        <span className={cn("relative h-6 w-11 rounded-full transition-colors", on ? "bg-win/70" : "bg-white/10")}>
          <span
            className={cn(
              "absolute top-0.5 h-5 w-5 rounded-full bg-bone shadow transition-all motion-reduce:transition-none",
              on ? "left-[1.375rem]" : "left-0.5",
            )}
          />
        </span>
      </button>
      {error ? <span className="text-xs text-loss-bright">{error}</span> : null}
    </div>
  );
}

function Stat({ label, value, sub, valueClass }: { label: string; value: React.ReactNode; sub?: string; valueClass?: string }) {
  return (
    <div className="rounded-md border border-border-soft bg-ink/40 px-3 py-2.5">
      <p className="text-[11px] text-muted">{label}</p>
      <p className={cn("mt-1 font-data text-base text-bone", valueClass)}>{value}</p>
      {sub ? <p className="mt-0.5 text-[11px] text-muted-dim">{sub}</p> : null}
    </div>
  );
}

function LeaderCard({ leader, live, broker, env }: { leader: LeaderSummary; live: boolean; broker?: string; env?: string }) {
  const st = leader.strategy;
  return (
    <Card className={cn("flex flex-col gap-5 p-5", leader.paused && "border-loss/40")}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 className="flex flex-wrap items-baseline gap-2 text-lg font-semibold text-bone">
            {leaderName(leader.label)}
            {leader.internal_label ? <span className="text-xs font-normal text-muted-dim">لقب داخلي — لا يوجد اسم علني</span> : null}
          </h2>
          <p className="mt-1 break-all font-data text-xs text-muted">{leader.address}</p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            <ModeBadge mode={leader.mode} />
            {leader.paused ? <Badge tone="loss">موقوف</Badge> : <Badge tone="win">مفعّل</Badge>}
            {leader.breaker ? <Badge tone="loss">قاطع التراجع مفعّل</Badge> : null}
            {leader.allow_execute ? <Badge tone="gold">التنفيذ مسموح</Badge> : <Badge>التنفيذ مقفل</Badge>}
          </div>
        </div>
        <LeaderSwitch leader={leader} />
      </div>

      {leader.paused ? (
        <p className="rounded border border-loss/30 bg-loss/5 px-3 py-2 text-xs leading-relaxed text-muted">
          لا يُفتح أي نسخ جديد لـ {leaderName(leader.label)} الآن. صفقاته المفتوحة تبقى، وتُغلق معه أو عند هدف الربح كالعادة.
        </p>
      ) : null}

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="صفقات مفتوحة" value={leader.open_copies} sub={leader.observed_open ? `+ ${leader.observed_open} محاكاة` : undefined} />
        <Stat
          label="صافي الربح"
          value={formatMoney(leader.realized_pnl)}
          valueClass={pnlClass(leader.realized_pnl)}
          sub={`${leader.closed} مغلقة · ${leader.wins} رابحة · ${leader.losses} خاسرة`}
        />
        <Stat
          label="ربح المحاكاة"
          value={formatMoney(leader.observed_pnl)}
          valueClass={pnlClass(leader.observed_pnl)}
          sub="افتراضي — لم يُنفّذ"
        />
        <Stat label="آخر حركة له" value={formatRelativeTime(leader.last_event_at)} sub={`${leader.leader_positions} مركز مفتوح عنده`} />
        <Stat label="حسابه على Hyperliquid" value={formatUsd(leader.account_value)} sub={`لقطة ${formatRelativeTime(leader.snapshot_at)}`} />
        <Stat
          label="حسابنا عند الوسيط"
          value={formatUsd(leader.equity)}
          sub={leader.shared_account ? "حساب مشترك مع متداول آخر" : brokerLabel(broker, env)}
        />
        <Stat label="الهامش لكل صفقة" value={`${formatNumber(Number(st.risk_per_trade) * 100)}%`} sub={`رافعة ${st.leverage}x معزولة`} />
        <Stat label="هدف الربح" value={`+${formatNumber(st.take_profit_move_pct, 2)}%`} sub={`حتى ${st.max_open_positions} مراكز`} />
      </div>

      {leader.shared_account ? (
        <p className="rounded border border-warn/30 bg-warn/5 px-3 py-2 text-xs leading-relaxed text-muted">
          هذا المتداول يتداول على نفس حساب Bybit مع متداول آخر. الحساب يحمل مركزًا واحدًا لكل عملة، فإذا كانت العملة مفتوحة
          للمتداول الآخر تُرفض صفقته عليها. الحل: حساب فرعي في Bybit لكل متداول.
        </p>
      ) : null}

      <div className="border-t border-border-soft pt-4">
        <h3 className="mb-3 text-sm font-semibold text-bone">وضع التشغيل</h3>
        <ModePicker key={leader.mode} leader={leader.label} current={leader.mode} allowExecute={leader.allow_execute} live={live} />
      </div>

      <div className="flex flex-wrap gap-2 border-t border-border-soft pt-4 text-xs">
        {[
          { href: `/loco?leader=${leader.label}`, label: "مراكزه الآن" },
          { href: `/trades?leader=${leader.label}`, label: "صفقاتنا معه" },
          { href: `/events?leader=${leader.label}`, label: "حركاته" },
          { href: `/analytics?leader=${leader.label}`, label: "تحليلاته" },
        ].map((l) => (
          <Link
            key={l.href}
            href={l.href}
            className="rounded border border-border px-2.5 py-1 text-muted transition-colors hover:border-gold-dim/60 hover:text-gold-bright"
          >
            {l.label}
          </Link>
        ))}
      </div>
    </Card>
  );
}

export default function LeadersPage() {
  const leaders = useLeaders();
  const overview = useOverview();
  const live = overview.data?.mode.live ?? false;

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-5">
      <div>
        <h1 className="text-xl font-semibold text-bone">المتداولين</h1>
        <p className="mt-1 text-sm leading-relaxed text-muted">
          كل متداول ننسخه يعمل مستقلًا: له وضعه، وزر تفعيل وإيقاف، وصفقاته. الإيقاف يمنع أي نسخ جديد له فقط، وإغلاقاته
          تُتابع دائمًا. أي متداول جديد يبدأ بوضع المراقبة، والتنفيذ الفعلي لكل متداول مقفل من السيرفر حتى تقرر فتحه.
        </p>
      </div>

      {leaders.isLoading ? (
        <LoadingState />
      ) : leaders.isError ? (
        <ErrorState message={(leaders.error as Error).message} />
      ) : !leaders.data?.length ? (
        <EmptyState icon={Users} title="لا يوجد متداولون مضافون" />
      ) : (
        leaders.data.map((l) => (
          <LeaderCard key={l.label} leader={l} live={live} broker={overview.data?.account.broker} env={overview.data?.account.env} />
        ))
      )}
    </div>
  );
}
