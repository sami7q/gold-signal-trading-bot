"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { connectLive } from "@/lib/live";
import { Menu } from "lucide-react";
import { Sidebar } from "@/components/shell/sidebar";
import { StatusStrip } from "@/components/shell/status-strip";
import { UserMenu } from "@/components/shell/user-menu";

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [navOpen, setNavOpen] = useState(false);

  // /login renders its own centered, chrome-free layout.
  const isLoginPage = pathname === "/login";
  const queryClient = useQueryClient();

  // botup.md §V11: one live stream to the bot while signed in -- pages refetch the moment something changes.
  useEffect(() => {
    if (isLoginPage) return;
    return connectLive(queryClient);
  }, [isLoginPage, queryClient]);

  // Lock background scroll while the mobile drawer is open, and let Escape close it.
  useEffect(() => {
    if (!navOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setNavOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [navOpen]);

  if (isLoginPage) {
    return <>{children}</>;
  }

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar open={navOpen} onClose={() => setNavOpen(false)} />

      {navOpen && (
        <div className="fixed inset-0 z-40 bg-black/60 lg:hidden" onClick={() => setNavOpen(false)} aria-hidden="true" />
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-12 shrink-0 items-center gap-3 border-b border-border-soft bg-ink/60 px-4 lg:hidden">
          <button
            type="button"
            onClick={() => setNavOpen(true)}
            aria-label="فتح القائمة"
            className="rounded p-1.5 text-muted hover:bg-white/5 hover:text-bone"
          >
            <Menu className="h-5 w-5" strokeWidth={1.75} />
          </button>
          <span className="text-sm font-bold text-gold-bright">بوت الفلوووووس</span>
          <div className="min-w-0 flex-1" />
          <UserMenu variant="mobile" />
        </div>

        <StatusStrip />
        <main className="flex-1 overflow-y-auto p-4 sm:p-6">{children}</main>
      </div>
    </div>
  );
}
