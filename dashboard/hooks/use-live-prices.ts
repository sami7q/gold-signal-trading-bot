"use client";

import { useEffect, useSyncExternalStore } from "react";
import { booksSnapshot, subscribeBooks, watchCoins, type Book } from "@/lib/hl-live";

const EMPTY: Record<string, Book> = {};

/** botup.md §V13: live book tops (bid / ask / mid) for these coins, ~4 renders a second at most. */
export function useLivePrices(coins: string[]): Record<string, Book> {
  const key = [...new Set(coins)].sort().join(",");
  useEffect(() => {
    if (!key) return;
    return watchCoins(key.split(","));
  }, [key]);
  return useSyncExternalStore(subscribeBooks, booksSnapshot, () => EMPTY);
}
