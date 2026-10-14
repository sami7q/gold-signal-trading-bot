"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutDashboard, ArrowLeftRight, Radar, Activity, LineChart, SlidersHorizontal, ScrollText, Users, Layers, FlaskConical, X } from "lucide-react";
import { cn } from "@/lib/format";
import { UserMenu } from "@/components/shell/user-menu";

const NAV_ITEMS = [
  { href: "/", label: "نظرة عامة", icon: LayoutDashboard },
  { href: "/leaders", label: "المتداولين", icon: Users },
  { href: "/portfolio", label: "الثلاثي الجديد (محاكاة)", icon: FlaskConical },
  { href: "/trio", label: "الثلاثي (مراقبة)", icon: Layers },
  { href: "/loco", label: "مراكز المتداولين", icon: Radar },
  { href: "/trades", label: "صفقاتنا", icon: ArrowLeftRight },
  { href: "/events", label: "حركات المتداولين", icon: Activity },
  { href: "/analytics", label: "التحليلات", icon: LineChart },
  { href: "/logs", label: "السجلات", icon: ScrollText },
  { href: "/settings", label: "التحكم والإعدادات", icon: SlidersHorizontal },
] as const;

export function Brand() {
  return (
    <span className="flex items-center gap-2">
      <span className="font-data text-lg font-semibold tracking-tight text-gold-bright">LOCO</span>
      <span className="text-sm font-medium text-muted">نسخ الصفقات</span>
    </span>
  );
}

export function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  const pathname = usePathname();

  return (
    <aside
      className={cn(
        "fixed inset-y-0 left-0 z-50 flex h-full w-64 shrink-0 -translate-x-full flex-col",
        "border-r border-border bg-surface transition-transform duration-200 ease-out",
        "lg:static lg:z-auto lg:w-56 lg:translate-x-0 lg:bg-surface/60",
        open && "translate-x-0",
      )}
    >
      <div className="flex items-center justify-between gap-2 border-b border-border-soft px-4 py-4">
        <Brand />
        <button
          type="button"
          onClick={onClose}
          aria-label="إغلاق القائمة"
          className="rounded p-1.5 text-muted hover:bg-white/5 hover:text-bone lg:hidden"
        >
          <X className="h-4 w-4" strokeWidth={1.75} />
        </button>
      </div>

      <div className="border-b border-border-soft px-2 py-2.5">
        <UserMenu variant="desktop" />
      </div>

      <nav className="flex-1 space-y-0.5 overflow-y-auto px-2 py-3">
        {NAV_ITEMS.map((item) => {
          const active = pathname === item.href;
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              onClick={onClose}
              className={cn(
                "flex items-center gap-2.5 rounded px-2.5 py-2.5 text-sm transition-colors lg:py-2",
                active ? "bg-gold/10 text-gold-bright" : "text-muted hover:bg-white/5 hover:text-bone",
              )}
            >
              <Icon className="h-4 w-4" strokeWidth={1.75} />
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="border-t border-border-soft px-4 py-3 text-[11px] leading-relaxed text-muted-dim">
        ينسخ صفقات متداولين على Hyperliquid إلى Bybit — لحظيًا، حسب الوضع المفعّل في صفحة التحكم.
      </div>
    </aside>
  );
}
