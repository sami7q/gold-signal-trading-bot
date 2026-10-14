// botup.md §V11 -- the real-time link to the bot: one Server-Sent Events stream (/api/live) that says what changed,
// so the dashboard refetches exactly that at once instead of polling. While the stream is up, polling drops to a
// slow safety net; if it drops, every query falls back to its normal polling by itself.

import type { QueryClient } from "@tanstack/react-query";
import { API_BASE_URL } from "@/lib/api";

export type LiveStatus = "off" | "connecting" | "live" | "offline";

export interface LiveSnapshot {
  rt: number | null; // simulator view written (every ~2 s, and after every fill)
  ev: number | null; // newest wallet fill
  ord: number | null; // newest simulated order
  tr: number | null; // newest closed trade
  al: number | null; // newest advisor alert
  an: number | null; // newest advisor analysis
  log: number | null; // newest log line
  cp: number | null; // newest copy-engine event
}

/** Safety-net polling while the stream is live. */
export const LIVE_SAFETY_MS = 60_000;
/** The heavy simulator views are refetched at most this often from the stream's view ticks. */
const VIEW_THROTTLE_MS = 2_000;

let status: LiveStatus = "off";
let lastEventAt = 0;
const listeners = new Set<() => void>();

function setStatus(next: LiveStatus) {
  if (next === status) return;
  status = next;
  listeners.forEach((l) => l());
}

export function liveStatus(): LiveStatus {
  return status;
}

export function lastLiveEventAt(): number {
  return lastEventAt;
}

export function subscribeLive(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** For a react-query `refetchInterval`: the normal interval, or the slow safety net while the stream is live. */
export function livePoll(ms: number): () => number {
  return () => (status === "live" ? Math.max(ms, LIVE_SAFETY_MS) : ms);
}

const changed = (a: LiveSnapshot | null, b: LiveSnapshot, keys: (keyof LiveSnapshot)[]) =>
  a !== null && keys.some((k) => a[k] !== b[k]);

/** Opens the stream; returns the function that closes it. EventSource reconnects by itself (retry 2 s). */
export function connectLive(queryClient: QueryClient): () => void {
  if (typeof window === "undefined" || typeof EventSource === "undefined") return () => {};
  let prev: LiveSnapshot | null = null;
  let lastView = 0;
  let viewTimer: ReturnType<typeof setTimeout> | null = null;
  setStatus("connecting");
  const es = new EventSource(`${API_BASE_URL}/api/live`, { withCredentials: true });

  const refreshViews = () => {
    lastView = Date.now();
    void queryClient.invalidateQueries({ queryKey: ["portfolio"] });
    void queryClient.invalidateQueries({ queryKey: ["portfolio-compare"] });
    void queryClient.invalidateQueries({ queryKey: ["trade-chart"] });
  };

  es.onopen = () => setStatus("live");
  es.onerror = () => setStatus(es.readyState === EventSource.CLOSED ? "offline" : "connecting");
  es.addEventListener("auth", () => {
    es.close();
    setStatus("offline");
  });
  es.onmessage = (msg) => {
    let snap: LiveSnapshot;
    try {
      snap = JSON.parse(msg.data) as LiveSnapshot;
    } catch {
      return;
    }
    lastEventAt = Date.now();
    setStatus("live");
    if (changed(prev, snap, ["ev", "ord", "tr"])) {
      refreshViews(); // a fill or an order: at once
      void queryClient.invalidateQueries({ queryKey: ["advice-alerts"] });
    } else if (changed(prev, snap, ["rt"])) {
      const wait = VIEW_THROTTLE_MS - (Date.now() - lastView);
      if (wait <= 0) refreshViews();
      else if (!viewTimer) {
        viewTimer = setTimeout(() => {
          viewTimer = null;
          refreshViews();
        }, wait);
      }
    }
    if (changed(prev, snap, ["al", "an"])) {
      void queryClient.invalidateQueries({ queryKey: ["advice"] });
      void queryClient.invalidateQueries({ queryKey: ["advice-alerts"] });
    }
    if (changed(prev, snap, ["log"])) void queryClient.invalidateQueries({ queryKey: ["logs"] });
    if (changed(prev, snap, ["cp"])) {
      for (const key of ["overview", "events", "copies", "loco", "leaders", "leader-positions", "virtual", "analytics"]) {
        void queryClient.invalidateQueries({ queryKey: [key] });
      }
    }
    prev = snap;
  };

  return () => {
    if (viewTimer) clearTimeout(viewTimer);
    es.close();
    setStatus("off");
  };
}
