"use client";

import { useState } from "react";
import { Minus, Plus, RotateCcw, Send, Target, TriangleAlert } from "lucide-react";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { LoadingState } from "@/components/ui/empty-state";
import {
  useResetSimulation,
  useSendMockMessage,
  useSetMarketPrice,
  useSimulationState,
  useTriggerStopLoss,
  useTriggerTakeProfit,
} from "@/hooks/use-simulation";
import { formatPrice } from "@/lib/format";

const TEMPLATES = [
  { label: "BUY GOLD", text: "BUY GOLD" },
  { label: "Entry + SL", text: "4640 - 4637\nSL 4623" },
  { label: "Targets", text: "4644\n4650\n4663\n4677\n4700" },
];

export default function SimulationPage() {
  const stateQuery = useSimulationState();
  const sendMessage = useSendMockMessage();
  const setPrice = useSetMarketPrice();
  const triggerTp = useTriggerTakeProfit();
  const triggerSl = useTriggerStopLoss();
  const reset = useResetSimulation();

  const [messageText, setMessageText] = useState("BUY GOLD");
  const [priceInput, setPriceInput] = useState("");
  const [lastResult, setLastResult] = useState<string | null>(null);

  const state = stateQuery.data;

  function handleSend() {
    if (!messageText.trim()) return;
    sendMessage.mutate(
      { text: messageText },
      {
        onSuccess: (result) => {
          setLastResult(
            result.duplicate
              ? "Duplicate — ignored, no new signal."
              : `${result.classification ?? "IGNORED"} → signal ${result.signal_status ?? "n/a"}${
                  result.became_ready ? " (ready → engine evaluated)" : ""
                }`,
          );
        },
      },
    );
  }

  function handleSetPrice() {
    if (!priceInput) return;
    setPrice.mutate({ bid: priceInput }, { onSuccess: () => setPriceInput("") });
  }

  function handleMove(delta: string) {
    setPrice.mutate({ delta });
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold text-bone">Simulation</h1>
        <p className="mt-1 text-sm text-muted">
          Drive the entire pipeline from the browser — mock signals, a controllable price, and a paper
          account. This is how the system gets proven before Telegram or MT5 ever connect.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader title="Mock Telegram Message" subtitle="Feeds the parser exactly like a real channel message" />
          <CardBody className="flex flex-col gap-3">
            <div className="flex flex-wrap gap-2">
              {TEMPLATES.map((t) => (
                <Button key={t.label} size="sm" variant="ghost" onClick={() => setMessageText(t.text)}>
                  {t.label}
                </Button>
              ))}
            </div>
            <Textarea
              rows={4}
              className="font-data"
              value={messageText}
              onChange={(e) => setMessageText(e.target.value)}
              placeholder={"BUY GOLD"}
            />
            <div className="flex items-center gap-3">
              <Button variant="primary" onClick={handleSend} disabled={sendMessage.isPending}>
                <Send className="h-3.5 w-3.5" />
                Send Signal
              </Button>
              {sendMessage.isPending && <span className="text-xs text-muted">Sending…</span>}
            </div>
            {lastResult && <p className="font-data text-xs text-gold-bright">{lastResult}</p>}
            {sendMessage.isError && (
              <p className="text-xs text-loss-bright">{(sendMessage.error as Error).message}</p>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Simulated Market" subtitle={state ? state.symbol : "—"} />
          <CardBody className="flex flex-col gap-4">
            {stateQuery.isLoading || !state ? (
              <LoadingState />
            ) : (
              <>
                <div className="flex items-end gap-6">
                  <div>
                    <div className="text-[11px] uppercase tracking-wide text-muted">Bid</div>
                    <div className="font-data text-xl text-bone">{formatPrice(state.bid)}</div>
                  </div>
                  <div>
                    <div className="text-[11px] uppercase tracking-wide text-muted">Ask</div>
                    <div className="font-data text-xl text-gold-bright">{formatPrice(state.ask)}</div>
                  </div>
                  <div className="ml-auto text-right">
                    <div className="text-[11px] uppercase tracking-wide text-muted">Equity</div>
                    <div className="font-data text-xl text-bone">${formatPrice(state.equity)}</div>
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  <Button size="sm" variant="secondary" onClick={() => handleMove("-1")}>
                    <Minus className="h-3.5 w-3.5" /> 1
                  </Button>
                  <Button size="sm" variant="secondary" onClick={() => handleMove("-10")}>
                    <Minus className="h-3.5 w-3.5" /> 10
                  </Button>
                  <Button size="sm" variant="secondary" onClick={() => handleMove("10")}>
                    <Plus className="h-3.5 w-3.5" /> 10
                  </Button>
                  <Button size="sm" variant="secondary" onClick={() => handleMove("1")}>
                    <Plus className="h-3.5 w-3.5" /> 1
                  </Button>
                  <div className="ml-auto flex items-center gap-2">
                    <Input
                      className="w-28 font-data"
                      placeholder="Set bid…"
                      value={priceInput}
                      onChange={(e) => setPriceInput(e.target.value)}
                    />
                    <Button size="sm" onClick={handleSetPrice} disabled={setPrice.isPending}>
                      Set
                    </Button>
                  </div>
                </div>

                <div className="flex flex-wrap gap-2 border-t border-border-soft pt-3">
                  <Button size="sm" variant="secondary" onClick={() => triggerTp.mutate()} disabled={triggerTp.isPending}>
                    <Target className="h-3.5 w-3.5 text-win-bright" /> Trigger TP
                  </Button>
                  <Button size="sm" variant="secondary" onClick={() => triggerSl.mutate()} disabled={triggerSl.isPending}>
                    <TriangleAlert className="h-3.5 w-3.5 text-loss-bright" /> Trigger SL
                  </Button>
                </div>

                <div>
                  <div className="mb-1.5 text-[11px] uppercase tracking-wide text-muted">
                    Open Positions ({state.open_positions.length})
                  </div>
                  {state.open_positions.length === 0 ? (
                    <p className="text-xs text-muted-dim">None open.</p>
                  ) : (
                    <ul className="flex flex-col gap-1.5">
                      {state.open_positions.map((p) => (
                        <li
                          key={p.position_id}
                          className="font-data flex items-center justify-between rounded border border-border-soft bg-ink/40 px-2.5 py-1.5 text-xs"
                        >
                          <span className="text-bone">
                            #{p.position_id} {p.side} {formatPrice(p.lot)} @ {formatPrice(p.entry_price)}
                          </span>
                          <span className="text-muted">
                            SL {formatPrice(p.stop_loss)} · TP {formatPrice(p.take_profit)}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </>
            )}
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader title="Reset" subtitle="Clears simulated state — never available once Trading Mode is LIVE" />
        <CardBody className="flex flex-wrap gap-3">
          <Button variant="secondary" onClick={() => reset.mutate("account")} disabled={reset.isPending}>
            <RotateCcw className="h-3.5 w-3.5" /> Reset Account
          </Button>
          <Button variant="secondary" onClick={() => reset.mutate("trades")} disabled={reset.isPending}>
            <RotateCcw className="h-3.5 w-3.5" /> Reset Trades &amp; Signals
          </Button>
          <Button variant="danger" onClick={() => reset.mutate("all")} disabled={reset.isPending}>
            <RotateCcw className="h-3.5 w-3.5" /> Reset Everything
          </Button>
        </CardBody>
      </Card>
    </div>
  );
}
