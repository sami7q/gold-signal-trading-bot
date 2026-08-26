"use client";

import { useState } from "react";
import { AlertTriangle, Check } from "lucide-react";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input, Select } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { LoadingState } from "@/components/ui/empty-state";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { usePipPreview, useSettings, useUpdateSettings } from "@/hooks/use-settings";
import { formatPrice } from "@/lib/format";
import type { TradingMode, TradingSettingsUpdate } from "@/types/api";

const LIVE_PHRASE = "ENABLE LIVE TRADING";

function Row({
  label,
  description,
  children,
}: {
  label: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 border-b border-border-soft py-4 last:border-0 sm:flex-row sm:items-center sm:justify-between">
      <div className="sm:max-w-sm">
        <p className="text-sm font-medium text-bone">{label}</p>
        <p className="mt-0.5 text-xs text-muted">{description}</p>
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

function PipPreviewPanel({ targetPips }: { targetPips: string }) {
  const debounced = useDebouncedValue(targetPips, 400);
  const previewQuery = usePipPreview(debounced || "0");
  const preview = previewQuery.data;

  if (!targetPips || Number(targetPips) <= 0) {
    return null;
  }

  return (
    <div className="rounded border border-border-soft bg-ink/40 px-3 py-2.5 text-xs">
      <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-muted">
        Pip Preview — same math the Trading Engine uses, not recomputed here
      </p>
      {previewQuery.isLoading ? (
        <p className="text-muted-dim">Calculating…</p>
      ) : !preview || !preview.broker_healthy || preview.pip_size === null ? (
        <p className="text-muted-dim">Broker unavailable — preview requires a connected broker.</p>
      ) : (
        <dl className="font-data grid grid-cols-2 gap-x-6 gap-y-1 sm:grid-cols-3">
          <div className="flex justify-between gap-2 sm:block">
            <dt className="text-muted-dim">Target</dt>
            <dd className="text-bone">{formatPrice(preview.target_pips)} pips</dd>
          </div>
          <div className="flex justify-between gap-2 sm:block">
            <dt className="text-muted-dim">Symbol Point</dt>
            <dd className="text-bone">{preview.point}</dd>
          </div>
          <div className="flex justify-between gap-2 sm:block">
            <dt className="text-muted-dim">Pip Size</dt>
            <dd className="text-bone">{preview.pip_size}</dd>
          </div>
          <div className="flex justify-between gap-2 sm:block">
            <dt className="text-muted-dim">Price Distance</dt>
            <dd className="text-gold-bright">{preview.price_distance}</dd>
          </div>
          {preview.example_entry && (
            <div className="flex justify-between gap-2 sm:block">
              <dt className="text-muted-dim">Example Entry</dt>
              <dd className="text-bone">{formatPrice(preview.example_entry)}</dd>
            </div>
          )}
          {preview.calculated_take_profit && (
            <div className="flex justify-between gap-2 sm:block">
              <dt className="text-muted-dim">Calculated TP</dt>
              <dd className="text-win-bright">{formatPrice(preview.calculated_take_profit)}</dd>
            </div>
          )}
        </dl>
      )}
    </div>
  );
}

export default function SettingsPage() {
  const settingsQuery = useSettings();
  const updateSettings = useUpdateSettings();

  const [form, setForm] = useState<TradingSettingsUpdate | null>(null);
  const [syncedId, setSyncedId] = useState<number | null>(null);
  const [confirmPhrase, setConfirmPhrase] = useState("");
  const [savedFlash, setSavedFlash] = useState(false);

  // Sync the editable form from the fetched settings once (and again if the
  // record itself changes underneath us) — done during render, per React's
  // guidance for adjusting state from props, rather than in a useEffect.
  if (settingsQuery.data && settingsQuery.data.id !== syncedId) {
    setSyncedId(settingsQuery.data.id);
    const rest: Partial<typeof settingsQuery.data> = { ...settingsQuery.data };
    delete rest.id;
    setForm(rest);
  }

  if (settingsQuery.isLoading || !form) {
    return <LoadingState />;
  }

  const original = settingsQuery.data!;
  const switchingToLive = form.mode === "LIVE" && original.mode !== "LIVE";
  const canSave = !switchingToLive || confirmPhrase === LIVE_PHRASE;

  function set<K extends keyof TradingSettingsUpdate>(key: K, value: TradingSettingsUpdate[K]) {
    setForm((prev) => (prev ? { ...prev, [key]: value } : prev));
    setSavedFlash(false);
  }

  function handleSave() {
    if (!form) return;
    const payload: TradingSettingsUpdate = { ...form };
    if (switchingToLive) payload.confirm_live_phrase = confirmPhrase;
    updateSettings.mutate(payload, {
      onSuccess: () => {
        setConfirmPhrase("");
        setSavedFlash(true);
        setTimeout(() => setSavedFlash(false), 2500);
      },
    });
  }

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-bone">Trading Settings</h1>
        <p className="mt-1 text-sm text-muted">
          Every change here is written to the audit log. These are the only knobs the bot obeys —
          Telegram&apos;s own take-profit targets are never used for execution.
        </p>
      </div>

      <Card>
        <CardHeader title="Execution" subtitle="What the bot is allowed to do right now" />
        <CardBody className="p-0 px-4">
          <Row label="Auto Trading" description="Master switch. Off means no trade is ever opened, no matter what else is configured.">
            <Switch checked={!!form.auto_trading} onChange={(v) => set("auto_trading", v)} label="Auto trading" />
          </Row>
          <Row label="BUY Signals" description="Allow the bot to act on BUY GOLD signals.">
            <Switch checked={!!form.buy_enabled} onChange={(v) => set("buy_enabled", v)} label="Buy enabled" />
          </Row>
          <Row label="Require Stop Loss" description="Reject any signal missing a channel Stop Loss instead of trading unprotected.">
            <Switch
              checked={!!form.require_stop_loss}
              onChange={(v) => set("require_stop_loss", v)}
              label="Require stop loss"
            />
          </Row>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Risk" subtitle="Applied to every trade, regardless of what Telegram suggests" />
        <CardBody className="p-0 px-4">
          <Row label="Lot Size" description="Fixed size for every BUY order.">
            <Input
              type="number"
              step="0.01"
              min="0"
              className="w-28 text-right font-data"
              value={form.lot_size ?? ""}
              onChange={(e) => set("lot_size", e.target.value)}
            />
          </Row>
          <Row label="Take Profit (pips)" description="Our own target — Telegram's TP1/TP2/TP3 messages are stored but ignored.">
            <Input
              type="number"
              step="1"
              min="0"
              className="w-28 text-right font-data"
              value={form.target_pips ?? ""}
              onChange={(e) => set("target_pips", e.target.value)}
            />
          </Row>
          <div className="py-3">
            <PipPreviewPanel targetPips={form.target_pips ?? ""} />
          </div>
          <Row label="Maximum Open Trades" description="Blocks a new BUY while this many are already open.">
            <Input
              type="number"
              step="1"
              min="1"
              className="w-28 text-right font-data"
              value={form.max_open_trades ?? ""}
              onChange={(e) => set("max_open_trades", Number(e.target.value))}
            />
          </Row>
          <Row label="Signal Completion Timeout" description="Seconds to wait for a Stop Loss before a signal expires unexecuted.">
            <Input
              type="number"
              step="1"
              min="1"
              className="w-28 text-right font-data"
              value={form.signal_completion_timeout_seconds ?? ""}
              onChange={(e) => set("signal_completion_timeout_seconds", Number(e.target.value))}
            />
          </Row>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Trading Mode" subtitle="SIMULATION → DEMO → LIVE" />
        <CardBody className="flex flex-col gap-4 px-4">
          <Row label="Mode" description="Controls which broker adapter receives orders.">
            <Select value={form.mode} onChange={(e) => set("mode", e.target.value as TradingMode)}>
              <option value="SIMULATION">SIMULATION</option>
              <option value="DEMO">DEMO</option>
              <option value="LIVE">LIVE</option>
            </Select>
          </Row>

          {switchingToLive && (
            <div className="rounded border border-loss/40 bg-loss/10 p-3">
              <div className="flex items-center gap-2 text-sm font-medium text-loss-bright">
                <AlertTriangle className="h-4 w-4" />
                This enables real-money trading
              </div>
              <p className="mt-1 text-xs text-muted">
                Simulation endpoints will be disabled immediately. Type{" "}
                <span className="font-data text-bone">{LIVE_PHRASE}</span> to confirm.
              </p>
              <Input
                className="mt-2 font-data"
                placeholder={LIVE_PHRASE}
                value={confirmPhrase}
                onChange={(e) => setConfirmPhrase(e.target.value)}
              />
            </div>
          )}
        </CardBody>
      </Card>

      {updateSettings.isError && (
        <p className="text-sm text-loss-bright">{(updateSettings.error as Error).message}</p>
      )}

      <div className="flex items-center gap-3">
        <Button variant="primary" onClick={handleSave} disabled={!canSave || updateSettings.isPending}>
          {updateSettings.isPending ? "Saving…" : "Save changes"}
        </Button>
        {savedFlash && (
          <span className="flex items-center gap-1 text-sm text-win-bright">
            <Check className="h-4 w-4" /> Saved
          </span>
        )}
      </div>
    </div>
  );
}
