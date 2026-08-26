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
} from "lucide-react";
import { cn } from "@/lib/format";

const NAV_ITEMS = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/trades", label: "Trades", icon: ArrowLeftRight },
  { href: "/signals", label: "Signals", icon: Radio },
  { href: "/analytics", label: "Analytics", icon: LineChart },
  { href: "/simulation", label: "Simulation", icon: FlaskConical },
  { href: "/logs", label: "Logs", icon: ScrollText },
  { href: "/settings", label: "Settings", icon: SlidersHorizontal },
] as const;

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="flex h-full w-56 shrink-0 flex-col border-r border-border bg-surface/60">
      <div className="flex items-center gap-2 border-b border-border-soft px-4 py-4">
        <span className="font-data text-lg font-semibold tracking-tight text-gold-bright">
          XAU
        </span>
        <span className="text-sm font-medium text-muted">Signal Bot</span>
      </div>

      <nav className="flex-1 space-y-0.5 px-2 py-3">
        {NAV_ITEMS.map((item) => {
          const active = pathname === item.href;
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-2.5 rounded px-2.5 py-2 text-sm transition-colors",
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
        Simulation-first build. No real Telegram or MT5 connection until explicitly enabled.
      </div>
    </aside>
  );
}
