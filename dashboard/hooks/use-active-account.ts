"use client";

import { useSyncExternalStore } from "react";
import { getActiveAccountId, subscribeActiveAccountId } from "@/lib/account";

/**
 * The currently-selected account id, reactively -- re-renders the calling
 * component the instant `setActiveAccountId`/`clearActiveAccountId` runs
 * anywhere in the app (switcher, accounts page, login), instead of only
 * updating whenever that component happens to re-render for some other
 * reason. `getServerSnapshot` returns `null` (no cookie exists during SSR).
 */
export function useActiveAccountId(): string | null {
  return useSyncExternalStore(subscribeActiveAccountId, getActiveAccountId, () => null);
}
