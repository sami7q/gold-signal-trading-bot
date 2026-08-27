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
import { arSide, formatPrice } from "@/lib/format";

const TEMPLATES = [
  { label: "شراء ذهب", text: "BUY GOLD" },
  { label: "دخول + وقف خسارة", text: "4640 - 4637\nSL 4623" },
  { label: "أهداف", text: "4644\n4650\n4663\n4677\n4700" },
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
              ? "مكررة — تم تجاهلها، لا إشارة جديدة."
              : `${result.classification ?? "IGNORED"} ← حالة الإشارة ${result.signal_status ?? "غير متاح"}${
                  result.became_ready ? " (جاهزة ← تم تقييمها بالمحرك)" : ""
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
        <h1 className="text-xl font-semibold text-bone">المحاكاة</h1>
        <p className="mt-1 text-sm text-muted">
          شغّل السلسلة بأكملها من المتصفح — إشارات تجريبية، سعر يمكن التحكم به، وحساب وهمي. هذي
          الطريقة اللي يُثبت فيها النظام قبل ربط تليكرام أو MT5.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader title="رسالة تيليكرام تجريبية" subtitle="تغذي المحلل تمامًا مثل رسالة قناة حقيقية" />
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
                إرسال الإشارة
              </Button>
              {sendMessage.isPending && <span className="text-xs text-muted">جارٍ الإرسال…</span>}
            </div>
            {lastResult && <p className="font-data text-xs text-gold-bright">{lastResult}</p>}
            {sendMessage.isError && (
              <p className="text-xs text-loss-bright">{(sendMessage.error as Error).message}</p>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="السوق التجريبي" subtitle={state ? state.symbol : "—"} />
          <CardBody className="flex flex-col gap-4">
            {stateQuery.isLoading || !state ? (
              <LoadingState />
            ) : (
              <>
                <div className="flex items-end gap-6">
                  <div>
                    <div className="text-[11px] uppercase tracking-wide text-muted">سعر البيع</div>
                    <div className="font-data text-xl text-bone">{formatPrice(state.bid)}</div>
                  </div>
                  <div>
                    <div className="text-[11px] uppercase tracking-wide text-muted">سعر الشراء</div>
                    <div className="font-data text-xl text-gold-bright">{formatPrice(state.ask)}</div>
                  </div>
                  <div className="ml-auto text-right">
                    <div className="text-[11px] uppercase tracking-wide text-muted">حقوق الملكية</div>
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
                      placeholder="عيّن سعر البيع…"
                      value={priceInput}
                      onChange={(e) => setPriceInput(e.target.value)}
                    />
                    <Button size="sm" onClick={handleSetPrice} disabled={setPrice.isPending}>
                      تعيين
                    </Button>
                  </div>
                </div>

                <div className="flex flex-wrap gap-2 border-t border-border-soft pt-3">
                  <Button size="sm" variant="secondary" onClick={() => triggerTp.mutate()} disabled={triggerTp.isPending}>
                    <Target className="h-3.5 w-3.5 text-win-bright" /> تفعيل هدف الربح
                  </Button>
                  <Button size="sm" variant="secondary" onClick={() => triggerSl.mutate()} disabled={triggerSl.isPending}>
                    <TriangleAlert className="h-3.5 w-3.5 text-loss-bright" /> تفعيل وقف الخسارة
                  </Button>
                </div>

                <div>
                  <div className="mb-1.5 text-[11px] uppercase tracking-wide text-muted">
                    الصفقات المفتوحة ({state.open_positions.length})
                  </div>
                  {state.open_positions.length === 0 ? (
                    <p className="text-xs text-muted-dim">لا يوجد شيء مفتوح.</p>
                  ) : (
                    <ul className="flex flex-col gap-1.5">
                      {state.open_positions.map((p) => (
                        <li
                          key={p.position_id}
                          className="font-data flex items-center justify-between rounded border border-border-soft bg-ink/40 px-2.5 py-1.5 text-xs"
                        >
                          <span className="text-bone">
                            #{p.position_id} {arSide(p.side)} {formatPrice(p.lot)} @ {formatPrice(p.entry_price)}
                          </span>
                          <span className="text-muted">
                            وقف الخسارة {formatPrice(p.stop_loss)} · هدف الربح {formatPrice(p.take_profit)}
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
        <CardHeader title="إعادة التصفير" subtitle="يمسح حالة المحاكاة — غير متاح أبدًا عندما يكون وضع التداول LIVE" />
        <CardBody className="flex flex-wrap gap-3">
          <Button variant="secondary" onClick={() => reset.mutate("account")} disabled={reset.isPending}>
            <RotateCcw className="h-3.5 w-3.5" /> إعادة تعيين الحساب
          </Button>
          <Button variant="secondary" onClick={() => reset.mutate("trades")} disabled={reset.isPending}>
            <RotateCcw className="h-3.5 w-3.5" /> إعادة تعيين الصفقات والإشارات
          </Button>
          <Button variant="danger" onClick={() => reset.mutate("all")} disabled={reset.isPending}>
            <RotateCcw className="h-3.5 w-3.5" /> إعادة تعيين كل شيء
          </Button>
        </CardBody>
      </Card>
    </div>
  );
}
