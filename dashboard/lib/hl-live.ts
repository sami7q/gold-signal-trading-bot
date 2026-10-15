// botup.md §V13 -- live prices in the browser, straight from Hyperliquid (public WebSocket, no key, any origin):
// one shared socket, `bbo` (best bid / ask, ~10 updates a second) for exactly the coins the open pages show.
// Pages re-render at most every RENDER_MS, so ten ticks a second never turns into ten renders a second.

const HL_WS = "wss://api.hyperliquid.xyz/ws";
const RENDER_MS = 250;
const STALE_MS = 5_000;

export interface Book {
  bid: number;
  ask: number;
  mid: number;
  t: number; // when we received it
}

let ws: WebSocket | null = null;
let retry = 500;
let snapshot: Record<string, Book> = {};
let dirty = false;
let timer: ReturnType<typeof setInterval> | null = null;
let ping: ReturnType<typeof setInterval> | null = null;
const wanted = new Map<string, number>(); // coin -> how many components want it
const subscribed = new Set<string>();
const listeners = new Set<() => void>();
const pending: Record<string, Book> = {};

function send(msg: unknown) {
  if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
}

function sync() {
  for (const coin of wanted.keys()) {
    if (!subscribed.has(coin)) {
      send({ method: "subscribe", subscription: { type: "bbo", coin } });
      subscribed.add(coin);
    }
  }
  for (const coin of [...subscribed]) {
    if (!wanted.has(coin)) {
      send({ method: "unsubscribe", subscription: { type: "bbo", coin } });
      subscribed.delete(coin);
    }
  }
}

function open() {
  if (ws || typeof window === "undefined" || !wanted.size) return;
  ws = new WebSocket(HL_WS);
  ws.onopen = () => {
    retry = 500;
    subscribed.clear();
    sync();
  };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data as string);
    if (msg.channel !== "bbo" || !msg.data) return;
    const [bid, ask] = msg.data.bbo ?? [];
    if (!bid || !ask) return;
    const b = +bid.px;
    const a = +ask.px;
    pending[msg.data.coin] = { bid: b, ask: a, mid: (a + b) / 2, t: Date.now() };
    dirty = true;
  };
  ws.onclose = () => {
    ws = null;
    subscribed.clear();
    if (wanted.size) setTimeout(open, (retry = Math.min(retry * 2, 10_000)));
  };
  ws.onerror = () => ws?.close();
  if (!ping) ping = setInterval(() => send({ method: "ping" }), 20_000);
  if (!timer) {
    timer = setInterval(() => {
      const now = Date.now();
      const stale = Object.keys(snapshot).filter((c) => now - snapshot[c].t > STALE_MS && !(c in pending));
      if (!dirty && !stale.length) return;
      dirty = false;
      const next = { ...snapshot, ...pending };
      for (const c of stale) delete next[c]; // a silent coin falls back to the bot's own mark
      for (const c of Object.keys(pending)) delete pending[c];
      snapshot = next;
      listeners.forEach((l) => l());
    }, RENDER_MS);
  }
}

function close() {
  ws?.close();
  ws = null;
  if (timer) clearInterval(timer);
  if (ping) clearInterval(ping);
  timer = ping = null;
}

export function watchCoins(coins: string[]): () => void {
  for (const c of coins) wanted.set(c, (wanted.get(c) ?? 0) + 1);
  if (ws) sync();
  else open();
  return () => {
    for (const c of coins) {
      const n = (wanted.get(c) ?? 1) - 1;
      if (n <= 0) wanted.delete(c);
      else wanted.set(c, n);
    }
    if (!wanted.size) close();
    else sync();
  };
}

export function subscribeBooks(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function booksSnapshot(): Record<string, Book> {
  return snapshot;
}

