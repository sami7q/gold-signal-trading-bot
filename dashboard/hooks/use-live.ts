"use client";

import { useSyncExternalStore } from "react";
import { liveStatus, subscribeLive, type LiveStatus } from "@/lib/live";

/** botup.md §V11: the state of the real-time stream to the bot (for the status strip). */
export function useLiveStatus(): LiveStatus {
  return useSyncExternalStore(subscribeLive, liveStatus, () => "off");
}
