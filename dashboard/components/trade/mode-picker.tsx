"use client";

import { useState } from "react";
import { AlertTriangle, Check, Lock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useSetMode } from "@/hooks/use-loco";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/format";
import type { Mode } from "@/types/api";

export const EXECUTE_CONFIRM = "ENABLE EXECUTION";

const MODES: Array<{ mode: Mode; title: string; body: string }> = [
  { mode: "WATCH", title: "مراقبة فقط", body: "يتابع المتداول ويسجّل كل حركة، بدون أي صفقة." },
  { mode: "OBSERVE", title: "محاكاة", body: "يحسب كل صفقة كأنه سينفّذها ويسجّلها افتراضيًا — لا يُرسل شيء للوسيط." },
  { mode: "EXECUTE", title: "تنفيذ فعلي", body: "يفتح ويغلق الصفقات فعليًا على حساب الوسيط لحظة ما يتحرك المتداول." },
];

/** Watch / simulate / execute for one leader. EXECUTE stays locked while the server's gate for him is closed. */
export function ModePicker({ leader, current, allowExecute, live }: {
  leader: string;
  current: Mode;
  allowExecute: boolean;
  live: boolean;
}) {
  const setMode = useSetMode();
  const [picked, setPicked] = useState<Mode>(current);
  const [confirm, setConfirm] = useState("");
  const needsConfirm = picked === "EXECUTE" && current !== "EXECUTE";
  const canApply = picked !== current && (!needsConfirm || confirm.trim() === EXECUTE_CONFIRM);
  const error = setMode.error ? (setMode.error instanceof ApiError ? setMode.error.message : "تعذّر تنفيذ الطلب.") : null;

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        {MODES.map((m) => {
          const locked = m.mode === "EXECUTE" && !allowExecute;
          return (
            <button
              key={m.mode}
              type="button"
              disabled={locked}
              onClick={() => { setPicked(m.mode); setConfirm(""); }}
              className={cn(
                "rounded-md border p-3 text-right transition-colors disabled:cursor-not-allowed disabled:opacity-50",
                picked === m.mode ? "border-gold-dim bg-gold/10" : "border-border hover:border-gold-dim/60",
              )}
            >
              <span className="flex items-center justify-between gap-2">
                <span className={cn("text-sm font-semibold", picked === m.mode ? "text-gold-bright" : "text-bone")}>{m.title}</span>
                {current === m.mode ? <Check className="h-4 w-4 text-win-bright" strokeWidth={2} /> : null}
                {locked ? <Lock className="h-3.5 w-3.5 text-muted-dim" strokeWidth={2} /> : null}
              </span>
              <span className="mt-1 block text-xs leading-relaxed text-muted">{m.body}</span>
            </button>
          );
        })}
      </div>

      {!allowExecute ? (
        <p className="text-xs leading-relaxed text-muted-dim">
          التنفيذ الفعلي مقفل لهذا المتداول من السيرفر (<span className="font-data">ALLOW_EXECUTE__{leader.toUpperCase()}</span>)،
          ويُفتح فقط بقرار منك بعد مراجعة نتائج المحاكاة.
        </p>
      ) : null}

      {needsConfirm ? (
        <div className="rounded border border-loss/40 bg-loss/10 p-3">
          <p className="flex items-center gap-2 text-sm font-medium text-loss-bright">
            <AlertTriangle className="h-4 w-4" />
            {live ? "هذا يفعّل التداول بأموال حقيقية." : "هذا يرسل أوامر فعلية لحساب الوسيط."}
          </p>
          <p className="mt-1 text-xs text-muted">
            اكتب <span className="font-data text-bone">{EXECUTE_CONFIRM}</span> للتأكيد.
          </p>
          <Input className="mt-2 font-data" placeholder={EXECUTE_CONFIRM} value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        </div>
      ) : null}

      {error ? <p className="text-sm text-loss-bright">{error}</p> : null}

      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant="primary"
          disabled={!canApply || setMode.isPending}
          onClick={() =>
            setMode.mutate(
              { leader, mode: picked, ...(needsConfirm ? { confirm: confirm.trim() } : {}) },
              { onSuccess: () => setConfirm("") },
            )
          }
        >
          {setMode.isPending ? "جارٍ التطبيق…" : "تطبيق الوضع"}
        </Button>
        <span className="text-xs text-muted-dim">يطبّقه البوت خلال ثوانٍ، بدون إعادة تشغيل.</span>
      </div>
    </div>
  );
}
