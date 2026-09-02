"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { ChevronsUpDown, Check, LogOut, Settings2 } from "lucide-react";
import Link from "next/link";
import { cn } from "@/lib/format";
import { useLogout, useMe } from "@/hooks/use-auth";
import { getActiveAccountId, setActiveAccountId } from "@/lib/account";
import type { MT5Account } from "@/types/api";

function LiveDot({ isLive }: { isLive: boolean }) {
  return (
    <span
      className={cn(
        "h-1.5 w-1.5 shrink-0 rounded-full",
        isLive ? "bg-win-bright" : "bg-muted-dim",
      )}
    />
  );
}

/** Shared list body used by both the desktop popover and the mobile sheet. */
function AccountList({
  accounts,
  activeId,
  onPick,
}: {
  accounts: MT5Account[];
  activeId: string | null;
  onPick: (account: MT5Account) => void;
}) {
  if (accounts.length === 0) {
    return (
      <p className="px-3 py-4 text-center text-xs text-muted-dim">
        لا توجد حسابات بعد — أضف أول حساب MT5 من صفحة الحسابات.
      </p>
    );
  }
  return (
    <ul className="max-h-64 overflow-y-auto py-1">
      {accounts.map((account) => {
        const isActive = String(account.id) === activeId;
        return (
          <li key={account.id}>
            <button
              type="button"
              onClick={() => onPick(account)}
              className={cn(
                "flex w-full items-center gap-2 px-3 py-2 text-right text-sm transition-colors",
                isActive ? "bg-gold/10 text-gold-bright" : "text-bone hover:bg-white/5",
              )}
            >
              <LiveDot isLive={account.is_live} />
              <span className="min-w-0 flex-1 truncate">{account.label}</span>
              <span className="font-data shrink-0 text-[11px] text-muted-dim">
                {account.mt5_login}
              </span>
              {isActive ? (
                <Check className="h-3.5 w-3.5 shrink-0 text-gold-bright" strokeWidth={2} />
              ) : null}
            </button>
          </li>
        );
      })}
    </ul>
  );
}

export function AccountSwitcher({ variant = "desktop" }: { variant?: "desktop" | "mobile" }) {
  const meQuery = useMe();
  const logout = useLogout();
  const router = useRouter();
  const queryClient = useQueryClient();

  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const activeId = getActiveAccountId();
  const accounts = meQuery.data?.accounts ?? [];
  const activeAccount = accounts.find((a) => String(a.id) === activeId);

  // Land on a valid account automatically -- covers a fresh login page
  // reload, or the previously-selected account having been archived.
  useEffect(() => {
    if (!meQuery.data) return;
    const stillValid = accounts.some((a) => String(a.id) === activeId);
    if (stillValid || accounts.length === 0) return;
    const preferred = accounts.find((a) => a.is_live) ?? accounts[0];
    setActiveAccountId(preferred.id);
    queryClient.invalidateQueries();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meQuery.data]);

  useEffect(() => {
    if (!open) return;
    function onClickOutside(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, [open]);

  function pick(account: MT5Account) {
    setActiveAccountId(account.id);
    setOpen(false);
    queryClient.invalidateQueries();
  }

  function handleLogout() {
    logout.mutate(undefined, {
      onSuccess: () => router.replace("/login"),
    });
  }

  if (!meQuery.data) {
    return (
      <div className={cn("animate-pulse rounded bg-white/5", variant === "desktop" ? "h-14" : "h-8 w-32")} />
    );
  }

  const label = activeAccount?.label ?? "اختر حسابًا";

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
        <LiveDot isLive={activeAccount?.is_live ?? false} />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-xs font-medium text-bone">{label}</span>
          {variant === "desktop" ? (
            <span className="block truncate text-[10px] text-muted-dim">
              {meQuery.data.user.display_name}
            </span>
          ) : null}
        </span>
        <ChevronsUpDown className="h-3.5 w-3.5 shrink-0 text-muted-dim" strokeWidth={1.75} />
      </button>

      {open ? (
        <div
          className={cn(
            "absolute z-50 mt-1.5 w-64 overflow-hidden rounded-md border border-border bg-surface shadow-xl",
            variant === "desktop" ? "bottom-full mb-1.5" : "left-0 top-full",
          )}
        >
          <div className="border-b border-border-soft px-3 py-2">
            <p className="truncate text-xs font-medium text-bone">{meQuery.data.user.display_name}</p>
            <p className="truncate text-[10px] text-muted-dim">@{meQuery.data.user.username}</p>
          </div>

          <AccountList accounts={accounts} activeId={activeId} onPick={pick} />

          <div className="border-t border-border-soft p-1">
            <Link
              href="/accounts"
              onClick={() => setOpen(false)}
              className="flex items-center gap-2 rounded px-2.5 py-2 text-xs text-muted transition-colors hover:bg-white/5 hover:text-bone"
            >
              <Settings2 className="h-3.5 w-3.5" strokeWidth={1.75} />
              إدارة الحسابات
            </Link>
            <button
              type="button"
              onClick={handleLogout}
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
