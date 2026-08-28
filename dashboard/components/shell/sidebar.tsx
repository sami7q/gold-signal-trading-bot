"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  ArrowLeftRight,
  Radio,
  LineChart,
  SlidersHorizontal,
  ScrollText,
  FlaskConical,
  Archive,
  X,
} from "lucide-react";
import { cn } from "@/lib/format";

const NAV_ITEMS = [
  { href: "/", label: "نظرة عامة", icon: LayoutDashboard },
  { href: "/trades", label: "الصفقات", icon: ArrowLeftRight },
  { href: "/signals", label: "الإشارات", icon: Radio },
  { href: "/analytics", label: "التحليلات", icon: LineChart },
  { href: "/archive", label: "الأرشيف", icon: Archive },
  { href: "/simulation", label: "المحاكاة", icon: FlaskConical },
  { href: "/logs", label: "السجلات", icon: ScrollText },
  { href: "/settings", label: "الإعدادات", icon: SlidersHorizontal },
] as const;

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
        <div className="flex items-center gap-2">
          <span className="font-data text-lg font-semibold tracking-tight text-gold-bright">
            XAU
          </span>
          <span className="text-sm font-medium text-muted">بوت الإشارات</span>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="إغلاق القائمة"
          className="rounded p-1.5 text-muted hover:bg-white/5 hover:text-bone lg:hidden"
        >
          <X className="h-4 w-4" strokeWidth={1.75} />
        </button>
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
                active
                  ? "bg-gold/10 text-gold-bright"
                  : "text-muted hover:bg-white/5 hover:text-bone",
              )}
            >
              <Icon className="h-4 w-4" strokeWidth={1.75} />
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="border-t border-border-soft px-4 py-3 text-[11px] leading-relaxed text-muted-dim">
        بوت تداول الذهب — إشارات تيليكرام حقيقية تُنفَّذ عبر MT5، حسب الأوضاع والإعدادات المفعّلة أدناه.
      </div>
    </aside>
  );
}
