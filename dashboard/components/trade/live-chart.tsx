"use client";

// botup.md §V7 -- the live trade chart: Hyperliquid candles (REST snapshot + WebSocket updates, straight from the
// browser: the API allows any origin and needs no key), the trader's fills (where he opened / added / reduced /
// closed) and our simulated orders as markers at their exact price, our average entry and the approximate
// liquidation price as horizontal lines. Library: TradingView Lightweight Charts 5.2.1 (Apache-2.0).

import { useEffect, useMemo, useRef, useState } from "react";
import type {
  CandlestickData,
  IChartApi,
  IPriceLine,
  ISeriesApi,
  ISeriesMarkersPluginApi,
  SeriesMarker,
  Time,
  UTCTimestamp,
} from "lightweight-charts";
import { useTradeChart } from "@/hooks/use-loco";
import { cn, formatMoney, formatNumber, formatPrice, formatUsd, pnlClass } from "@/lib/format";
import type { ChartFill, ChartOrder } from "@/types/api";

const HL_INFO = "https://api.hyperliquid.xyz/info";
const HL_WS = "wss://api.hyperliquid.xyz/ws";
const INTERVALS = [
  { key: "1m", ms: 60_000, label: "1د" },
  { key: "5m", ms: 300_000, label: "5د" },
  { key: "15m", ms: 900_000, label: "15د" },
  { key: "1h", ms: 3_600_000, label: "1س" },
  { key: "4h", ms: 14_400_000, label: "4س" },
  { key: "1d", ms: 86_400_000, label: "يوم" },
] as const;
type IntervalKey = (typeof INTERVALS)[number]["key"];
const BARS = 400;

const C = {
  bg: "#11141a",
  grid: "#1f242c",
  text: "#8b8f98",
  up: "#22a06b",
  down: "#e5484d",
  leaderBuy: "#4cc38a",
  leaderSell: "#f2777a",
  ours: "#c9a227",
  liq: "#e5484d",
};

const KIND_AR: Record<ChartFill["kind"], string> = {
  OPEN: "فتح",
  ADD: "تعزيز",
  REDUCE: "تقليص",
  CLOSE: "إغلاق",
  FLIP: "انعكاس",
};

const ORDER_AR: Record<string, string> = {
  OPEN: "فتحنا",
  ADD: "عزّزنا",
  REDUCE: "قلّصنا",
  CLOSE: "أغلقنا",
  FLIP_CLOSE: "أغلقنا (انعكاس)",
  FLIP_OPEN: "فتحنا (انعكاس)",
  RECONCILED: "إغلاق تصحيحي",
  LIQUIDATED: "تصفية",
};

interface HlCandle {
  t: number;
  o: string;
  h: string;
  l: string;
  c: string;
}

function toBar(k: HlCandle): CandlestickData<Time> {
  return { time: (k.t / 1000) as UTCTimestamp, open: +k.o, high: +k.h, low: +k.l, close: +k.c };
}

function bucket(t: number, ms: number): UTCTimestamp {
  return (Math.floor(t / ms) * ms / 1000) as UTCTimestamp;
}

/** The trader's fills, one marker per (candle, kind): many small fills of one order are one marker. */
function leaderMarkers(fills: ChartFill[], ms: number): SeriesMarker<Time>[] {
  const groups = new Map<string, { t: UTCTimestamp; kind: ChartFill["kind"]; side: string; sz: number; notional: number; n: number }>();
  for (const f of fills) {
    const t = bucket(f.t, ms);
    const key = `${t}|${f.kind}|${f.side}`;
    const g = groups.get(key) ?? { t, kind: f.kind, side: f.side, sz: 0, notional: 0, n: 0 };
    g.sz += f.sz;
    g.notional += f.sz * f.px;
    g.n += 1;
    groups.set(key, g);
  }
  return [...groups.values()].map((g) => ({
    time: g.t,
    position: "atPriceMiddle",
    price: g.notional / g.sz,
    shape: "circle",
    color: g.side === "B" ? C.leaderBuy : C.leaderSell,
    size: 1,
    text: `${KIND_AR[g.kind]}${g.n > 1 ? ` ×${g.n}` : ""}`,
  }));
}

/** Our simulated orders: arrows at their exact price, in gold. */
function ourMarkers(orders: ChartOrder[], ms: number): SeriesMarker<Time>[] {
  return orders.map((o) => ({
    time: bucket(o.t, ms),
    position: o.qty > 0 ? "atPriceBottom" : "atPriceTop",
    price: o.px,
    shape: o.qty > 0 ? "arrowUp" : "arrowDown",
    color: o.kind === "LIQUIDATED" ? C.liq : C.ours,
    size: 1.4,
    text: `${ORDER_AR[o.kind] ?? o.kind} ${formatUsd(o.notional)}`,
  }));
}

export function LiveTradeChart({
  wallet,
  coin,
  capital,
  scenario,
  group = "trio",
  leverage,
}: {
  wallet: string;
  coin: string;
  capital?: number;
  scenario: "real" | "base";
  group?: string;
  leverage?: number;
}) {
  const [interval, setInterval_] = useState<IntervalKey>("1h");
  const ms = INTERVALS.find((x) => x.key === interval)!.ms;
  const box = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const linesRef = useRef<IPriceLine[]>([]);
  const [status, setStatus] = useState<"loading" | "live" | "empty" | "error">("loading");
  const [last, setLast] = useState<number | null>(null);
  const q = useTradeChart(wallet, coin, capital, scenario, group, leverage);
  const data = q.data;

  // the chart + candles: rebuilt when the coin or the interval changes
  useEffect(() => {
    let disposed = false;
    let ws: WebSocket | null = null;
    let resize: ResizeObserver | null = null;
    // botup.md §V13: the last candle follows the book top (bbo, ~10 updates / s) between candle frames
    let lastBar: CandlestickData<Time> | null = null;
    let lastPaint = 0;
    (async () => {
      const lw = await import("lightweight-charts");
      if (disposed || !box.current) return;
      const chart = lw.createChart(box.current, {
        autoSize: true,
        layout: { background: { color: C.bg }, textColor: C.text, fontSize: 11 },
        grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
        rightPriceScale: { borderColor: C.grid },
        timeScale: { borderColor: C.grid, timeVisible: true, secondsVisible: false },
        crosshair: { mode: lw.CrosshairMode.Normal },
        // an explicit locale: the browser's default can be a tag Intl rejects (e.g. "en-US@posix")
        localization: { locale: "en-US" },
      });
      const series = chart.addSeries(lw.CandlestickSeries, {
        upColor: C.up,
        downColor: C.down,
        wickUpColor: C.up,
        wickDownColor: C.down,
        borderVisible: false,
        priceFormat: { type: "price", precision: 6, minMove: 0.000001 },
      });
      chartRef.current = chart;
      seriesRef.current = series;
      markersRef.current = lw.createSeriesMarkers(series, []);
      try {
        const now = Date.now();
        const r = await fetch(HL_INFO, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ type: "candleSnapshot", req: { coin, interval, startTime: now - BARS * ms, endTime: now } }),
        });
        const candles: HlCandle[] = await r.json();
        if (disposed) return;
        if (!Array.isArray(candles) || !candles.length) {
          setStatus("empty");
          return;
        }
        const bars = candles.map(toBar);
        const ref = bars[bars.length - 1].close;
        const decimals = Math.min(8, Math.max(2, 6 - Math.floor(Math.log10(Math.max(ref, 1e-9)))));
        series.applyOptions({ priceFormat: { type: "price", precision: decimals, minMove: Math.pow(10, -decimals) } });
        series.setData(bars);
        lastBar = { ...bars[bars.length - 1] };
        setLast(ref);
        chart.timeScale().scrollToRealTime();
        setStatus("live");
      } catch {
        if (!disposed) setStatus("error");
        return;
      }
      ws = new WebSocket(HL_WS);
      ws.onopen = () => {
        ws?.send(JSON.stringify({ method: "subscribe", subscription: { type: "candle", coin, interval } }));
        ws?.send(JSON.stringify({ method: "subscribe", subscription: { type: "bbo", coin } }));
      };
      ws.onmessage = (ev) => {
        const msg = JSON.parse(ev.data as string);
        if (msg.channel === "bbo" && msg.data?.coin === coin && lastBar) {
          const [bid, ask] = msg.data.bbo ?? [];
          if (!bid || !ask) return;
          const mid = (+bid.px + +ask.px) / 2;
          lastBar = { ...lastBar, close: mid, high: Math.max(lastBar.high, mid), low: Math.min(lastBar.low, mid) };
          seriesRef.current?.update(lastBar);
          const now = Date.now();
          if (now - lastPaint > 250) {
            lastPaint = now;
            setLast(mid);
          }
          return;
        }
        if (msg.channel !== "candle" || !msg.data) return;
        const k = msg.data as HlCandle & { s: string };
        if (k.s !== coin) return;
        const bar = toBar(k);
        lastBar = { ...bar };
        seriesRef.current?.update(bar);
        setLast(+k.c);
      };
      const ping = window.setInterval(() => ws?.readyState === 1 && ws.send(JSON.stringify({ method: "ping" })), 30_000);
      ws.onclose = () => window.clearInterval(ping);
      resize = new ResizeObserver(() => chart.timeScale().scrollToRealTime());
      resize.observe(box.current);
    })();
    return () => {
      disposed = true;
      ws?.close();
      resize?.disconnect();
      chartRef.current?.remove();
      chartRef.current = null;
      seriesRef.current = null;
      markersRef.current = null;
      linesRef.current = [];
    };
  }, [coin, interval, ms]);

  // markers + lines: refreshed with the bot's data (every 10 s)
  const markers = useMemo(() => {
    if (!data?.enabled) return [];
    return [...leaderMarkers(data.fills ?? [], ms), ...ourMarkers(data.orders ?? [], ms)].sort(
      (a, b) => (a.time as number) - (b.time as number),
    );
  }, [data, ms]);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series || status !== "live") return;
    markersRef.current?.setMarkers(markers);
    for (const line of linesRef.current) series.removePriceLine(line);
    linesRef.current = [];
    const pos = data?.position;
    if (pos) {
      linesRef.current.push(
        series.createPriceLine({ price: pos.entry, color: C.ours, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "متوسط دخولنا" }),
      );
    }
    if (data?.liq_price) {
      linesRef.current.push(
        series.createPriceLine({ price: data.liq_price, color: C.liq, lineWidth: 1, lineStyle: 1, axisLabelVisible: true, title: "تصفية تقريبية" }),
      );
    }
  }, [markers, data, status]);

  const pos = data?.position;
  const upnl = pos && last !== null ? pos.side * pos.qty * (last - pos.entry) : null;

  return (
    <div className="flex flex-col">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border-soft px-4 py-2.5">
        <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1 font-data text-sm">
          <span className="text-bone">{coin}</span>
          <span className="text-muted">السعر {last === null ? "—" : formatPrice(last)}</span>
          {pos ? (
            <>
              <span className={pos.side > 0 ? "text-win-bright" : "text-loss-bright"}>{pos.side > 0 ? "شراء" : "بيع"} {formatUsd(pos.notional)}</span>
              <span className="text-muted">دخولنا {formatPrice(pos.entry)}</span>
              <span className={cn(pnlClass(upnl ?? 0))}>غير محقق {formatMoney(upnl)}</span>
              <span className="text-muted">رسوم {formatMoney(-pos.fees)}</span>
              {data?.liq_price ? <span className="text-loss-bright">تصفية ~{formatPrice(data.liq_price)}</span> : null}
            </>
          ) : (
            <span className="text-muted-dim">لا مركز مفتوح لنا الآن على هذه العملة</span>
          )}
        </div>
        <div className="flex gap-1">
          {INTERVALS.map((x) => (
            <button
              key={x.key}
              type="button"
              onClick={() => {
                if (x.key !== interval) {
                  setStatus("loading");
                  setInterval_(x.key);
                }
              }}
              className={cn(
                "rounded px-2 py-1 font-data text-xs",
                interval === x.key ? "bg-gold/15 text-gold-bright" : "text-muted hover:text-bone",
              )}
            >
              {x.label}
            </button>
          ))}
        </div>
      </div>
      <div className="relative h-[420px]">
        <div ref={box} className="absolute inset-0" dir="ltr" />
        {status !== "live" ? (
          <div className="absolute inset-0 flex items-center justify-center text-sm text-muted">
            {status === "loading" ? "تحميل الشموع…" : status === "empty" ? "لا شموع لهذا السوق في هذا الإطار" : "تعذّر الوصول إلى Hyperliquid"}
          </div>
        ) : null}
      </div>
      <div className="flex flex-wrap gap-x-5 gap-y-1 border-t border-border-soft px-4 py-2 text-xs text-muted">
        <span><span style={{ color: C.leaderBuy }}>●</span> / <span style={{ color: C.leaderSell }}>●</span> تنفيذات المتداول (شراء / بيع) — فتح، تعزيز، تقليص، إغلاق</span>
        <span><span style={{ color: C.ours }}>▲▼</span> أوامرنا في الحساب الوهمي</span>
        <span><span style={{ color: C.ours }}>- -</span> متوسط دخولنا</span>
        <span><span style={{ color: C.liq }}>—</span> سعر التصفية التقريبي (لو تحرّكت هذه العملة وحدها)</span>
        <span>الشموع حيّة من Hyperliquid · {formatNumber(data?.fills?.length ?? 0, 0)} تنفيذًا للمتداول · {formatNumber(data?.orders?.length ?? 0, 0)} أمرًا لنا (آخر 7 أيام)</span>
      </div>
    </div>
  );
}
