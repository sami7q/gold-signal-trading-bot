"use client";

import { useQuery } from "@tanstack/react-query";

const HL_INFO = "https://api.hyperliquid.xyz/info";

/** One position of a trader's real Hyperliquid account (clearinghouseState). */
export interface WalletPosition {
  coin: string;
  /** signed size in coins: > 0 long, < 0 short */
  size: number;
  entry: number;
  notional: number;
  upnl: number;
  leverage: number | null;
  liq: number | null;
}

interface HlAssetPosition {
  position: {
    coin: string;
    szi: string;
    entryPx: string | null;
    positionValue: string;
    unrealizedPnl: string;
    leverage?: { value: number };
    liquidationPx: string | null;
  };
}

/** HIP-3 markets ("xyz:NVDA") live on their own dex: the account state is asked per dex. */
function dexOf(coin: string): string {
  const i = coin.indexOf(":");
  return i > 0 ? coin.slice(0, i) : "";
}

async function fetchPositions(address: string, dex: string): Promise<WalletPosition[]> {
  const r = await fetch(HL_INFO, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ type: "clearinghouseState", user: address, ...(dex ? { dex } : {}) }),
  });
  if (!r.ok) throw new Error(`Hyperliquid ${r.status}`);
  const state: { assetPositions?: HlAssetPosition[] } = await r.json();
  return (state.assetPositions ?? []).map(({ position: p }) => ({
    coin: p.coin,
    size: +p.szi,
    entry: p.entryPx === null ? 0 : +p.entryPx,
    notional: +p.positionValue,
    upnl: +p.unrealizedPnl,
    leverage: p.leverage?.value ?? null,
    liq: p.liquidationPx === null ? null : +p.liquidationPx,
  }));
}

/**
 * The traders' real open positions, straight from Hyperliquid (public API, no key, any origin), every 5 s:
 * key "address|coin". Only the dexes of the coins asked for are fetched.
 */
export function useWalletPositions(items: { address: string; coin: string }[]) {
  const pairs = [...new Set(items.map((x) => `${x.address}|${dexOf(x.coin)}`))].sort();
  return useQuery({
    queryKey: ["hl-wallet-positions", pairs.join(",")],
    enabled: pairs.length > 0,
    refetchInterval: 5_000,
    queryFn: async () => {
      const out: Record<string, WalletPosition> = {};
      await Promise.all(
        pairs.map(async (pair) => {
          const [address, dex] = pair.split("|");
          for (const p of await fetchPositions(address, dex)) out[`${address}|${p.coin}`] = p;
        }),
      );
      return out;
    },
  });
}
