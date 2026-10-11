"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ChevronsUpDown, LogOut } from "lucide-react";
import { cn } from "@/lib/format";
import { useLogout, useMe } from "@/hooks/use-auth";

export function UserMenu({ variant = "desktop" }: { variant?: "desktop" | "mobile" }) {
  const meQuery = useMe();
  const logout = useLogout();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onClickOutside(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, [open]);

  if (!meQuery.data) {
    return <div className={cn("animate-pulse rounded bg-white/5", variant === "desktop" ? "h-11" : "h-8 w-24")} />;
  }

  const { user } = meQuery.data;

  return (
    <div ref={rootRef} className={cn("relative", variant === "desktop" ? "w-full" : "")}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "flex items-center gap-2 rounded border border-border-soft bg-ink/40 text-right transition-colors hover:border-gold-dim/60",
          variant === "desktop" ? "w-full px-2.5 py-2" : "px-2 py-1",
        )}
      >
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-gold/15 text-[11px] font-semibold text-gold-bright">
          {user.display_name.slice(0, 1).toUpperCase()}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-xs font-medium text-bone">{user.display_name}</span>
          {variant === "desktop" ? <span className="block truncate text-[10px] text-muted-dim">@{user.username}</span> : null}
        </span>
        <ChevronsUpDown className="h-3.5 w-3.5 shrink-0 text-muted-dim" strokeWidth={1.75} />
      </button>

      {open ? (
        <div
          className={cn(
            "absolute top-full z-50 mt-1.5 w-56 overflow-hidden rounded-md border border-border bg-surface shadow-xl",
            variant === "desktop" ? "left-0" : "right-0",
          )}
        >
          <div className="p-1">
            <button
              type="button"
              onClick={() => logout.mutate(undefined, { onSuccess: () => router.replace("/login") })}
              disabled={logout.isPending}
              className="flex w-full items-center gap-2 rounded px-2.5 py-2 text-xs text-loss-bright transition-colors hover:bg-loss/10"
            >
              <LogOut className="h-3.5 w-3.5" strokeWidth={1.75} />
              تسجيل الخروج
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
