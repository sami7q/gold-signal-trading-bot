"use client";

import { useState } from "react";
import { Plus, Radio, Archive as ArchiveIcon, Pencil, X } from "lucide-react";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { EmptyState, LoadingState, ErrorState } from "@/components/ui/empty-state";
import {
  useAccounts,
  useArchiveAccount,
  useCreateAccount,
  useSetLiveAccount,
  useUpdateAccount,
} from "@/hooks/use-accounts";
import { setActiveAccountId } from "@/lib/account";
import { ApiError } from "@/lib/api";
import type { MT5Account, MT5AccountCreate, MT5AccountUpdate } from "@/types/api";

const LIVE_PHRASE = "ENABLE LIVE TRADING";

interface FormValues {
  label: string;
  mt5_login: string;
  mt5_server: string;
  mt5_password: string;
  symbol: string;
}

const EMPTY_FORM: FormValues = { label: "", mt5_login: "", mt5_server: "", mt5_password: "", symbol: "XAUUSD" };

function AccountFormFields({
  values,
  onChange,
  passwordOptional,
}: {
  values: FormValues;
  onChange: (values: FormValues) => void;
  passwordOptional?: boolean;
}) {
  function set<K extends keyof FormValues>(key: K, value: FormValues[K]) {
    onChange({ ...values, [key]: value });
  }
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <div className="sm:col-span-2">
        <Label htmlFor="label">اسم الحساب (للعرض فقط)</Label>
        <Input
          id="label"
          required
          placeholder="مثال: سامي — حساب رئيسي"
          value={values.label}
          onChange={(e) => set("label", e.target.value)}
        />
      </div>
      <div>
        <Label htmlFor="mt5_login">رقم حساب MT5</Label>
        <Input
          id="mt5_login"
          required
          className="font-data"
          value={values.mt5_login}
          onChange={(e) => set("mt5_login", e.target.value)}
        />
      </div>
      <div>
        <Label htmlFor="mt5_server">خادم MT5</Label>
        <Input
          id="mt5_server"
          required
          className="font-data"
          placeholder="مثال: ICMarketsSC-Demo"
          value={values.mt5_server}
          onChange={(e) => set("mt5_server", e.target.value)}
        />
      </div>
      <div>
        <Label htmlFor="mt5_password">كلمة مرور MT5</Label>
        <Input
          id="mt5_password"
          type="password"
          required={!passwordOptional}
          placeholder={passwordOptional ? "اتركه فارغًا للإبقاء عليها" : undefined}
          value={values.mt5_password}
          onChange={(e) => set("mt5_password", e.target.value)}
        />
      </div>
      <div>
        <Label htmlFor="symbol">الرمز</Label>
        <Input
          id="symbol"
          className="font-data"
          value={values.symbol}
          onChange={(e) => set("symbol", e.target.value)}
        />
      </div>
    </div>
  );
}

function AddAccountForm({ onDone }: { onDone: () => void }) {
  const createAccount = useCreateAccount();
  const [values, setValues] = useState<FormValues>(EMPTY_FORM);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const payload: MT5AccountCreate = { ...values };
    createAccount.mutate(payload, { onSuccess: () => onDone() });
  }

  const errorMessage =
    createAccount.error instanceof ApiError ? createAccount.error.message : null;

  return (
    <Card>
      <CardHeader
        title="إضافة حساب MT5 جديد"
        subtitle="بيانات دخول منفصلة تمامًا عن أي حساب آخر — لا أحد غيرك يراها."
        action={
          <button
            type="button"
            onClick={onDone}
            aria-label="إغلاق"
            className="rounded p-1 text-muted hover:bg-white/5 hover:text-bone"
          >
            <X className="h-4 w-4" strokeWidth={1.75} />
          </button>
        }
      />
      <CardBody>
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <AccountFormFields values={values} onChange={setValues} />
          {errorMessage ? (
            <p className="rounded border border-loss/40 bg-loss/10 px-2.5 py-2 text-xs text-loss-bright">
              {errorMessage}
            </p>
          ) : null}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={onDone}>
              إلغاء
            </Button>
            <Button type="submit" variant="primary" disabled={createAccount.isPending}>
              {createAccount.isPending ? "جارٍ الإضافة…" : "إضافة الحساب"}
            </Button>
          </div>
        </form>
      </CardBody>
    </Card>
  );
}

function EditAccountForm({ account, onDone }: { account: MT5Account; onDone: () => void }) {
  const updateAccount = useUpdateAccount();
  const [values, setValues] = useState<FormValues>({
    label: account.label,
    mt5_login: account.mt5_login,
    mt5_server: account.mt5_server,
    mt5_password: "",
    symbol: account.symbol,
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const patch: MT5AccountUpdate = {
      label: values.label,
      mt5_login: values.mt5_login,
      mt5_server: values.mt5_server,
      symbol: values.symbol,
      ...(values.mt5_password ? { mt5_password: values.mt5_password } : {}),
    };
    updateAccount.mutate({ id: account.id, patch }, { onSuccess: () => onDone() });
  }

  const errorMessage =
    updateAccount.error instanceof ApiError ? updateAccount.error.message : null;

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4 border-t border-border-soft p-4">
      <AccountFormFields values={values} onChange={setValues} passwordOptional />
      {errorMessage ? (
        <p className="rounded border border-loss/40 bg-loss/10 px-2.5 py-2 text-xs text-loss-bright">
          {errorMessage}
        </p>
      ) : null}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" onClick={onDone}>
          إلغاء
        </Button>
        <Button type="submit" variant="primary" disabled={updateAccount.isPending}>
          {updateAccount.isPending ? "جارٍ الحفظ…" : "حفظ التعديلات"}
        </Button>
      </div>
    </form>
  );
}

function SetLivePanel({ account, onDone }: { account: MT5Account; onDone: () => void }) {
  const setLiveAccount = useSetLiveAccount();
  const [phrase, setPhrase] = useState("");

  function handleConfirm() {
    setLiveAccount.mutate(
      { id: account.id, confirmPhrase: phrase },
      { onSuccess: () => onDone() },
    );
  }

  const errorMessage =
    setLiveAccount.error instanceof ApiError ? setLiveAccount.error.message : null;

  return (
    <div className="flex flex-col gap-2.5 border-t border-border-soft bg-warn/5 p-4">
      <p className="text-xs text-warn">
        هذا سيوقف تنفيذ أي حساب آخر مباشر حاليًا (لدى أي مستخدم) ويجعل هذا الحساب هو المتصل
        فعليًا بالبوت. اكتب العبارة التالية للتأكيد:{" "}
        <span className="font-data text-bone">{LIVE_PHRASE}</span>
      </p>
      <div className="flex flex-col gap-2 sm:flex-row">
        <Input
          value={phrase}
          onChange={(e) => setPhrase(e.target.value)}
          placeholder={LIVE_PHRASE}
          className="font-data"
        />
        <div className="flex gap-2">
          <Button
            variant="danger"
            disabled={phrase !== LIVE_PHRASE || setLiveAccount.isPending}
            onClick={handleConfirm}
          >
            تأكيد التفعيل
          </Button>
          <Button variant="ghost" onClick={onDone}>
            إلغاء
          </Button>
        </div>
      </div>
      {errorMessage ? <p className="text-xs text-loss-bright">{errorMessage}</p> : null}
    </div>
  );
}

function AccountCard({ account }: { account: MT5Account }) {
  const archiveAccount = useArchiveAccount();
  const [mode, setMode] = useState<"view" | "edit" | "set-live">("view");

  const archiveError =
    archiveAccount.error instanceof ApiError ? archiveAccount.error.message : null;

  function handleArchive() {
    if (!window.confirm(`أرشفة "${account.label}"؟ يمكن التراجع لاحقًا من الدعم الفني إن لزم.`)) {
      return;
    }
    archiveAccount.mutate(account.id);
  }

  function handleSwitchToThis() {
    setActiveAccountId(account.id);
  }

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            {account.label}
            {account.is_live ? <Badge tone="win">مباشر</Badge> : <Badge tone="neutral">غير مفعّل</Badge>}
            {!account.is_active ? <Badge tone="warn">مؤرشف</Badge> : null}
          </span>
        }
        subtitle={
          <span className="font-data">
            {account.mt5_login} · {account.mt5_server} · {account.symbol}
          </span>
        }
        action={
          <div className="flex flex-wrap items-center justify-end gap-1.5">
            <Button size="sm" variant="ghost" onClick={handleSwitchToThis}>
              التبديل إليه
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setMode(mode === "edit" ? "view" : "edit")}
            >
              <Pencil className="h-3.5 w-3.5" strokeWidth={1.75} />
              تعديل
            </Button>
            {!account.is_live ? (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setMode(mode === "set-live" ? "view" : "set-live")}
              >
                <Radio className="h-3.5 w-3.5" strokeWidth={1.75} />
                جعله مباشرًا
              </Button>
            ) : null}
            <Button
              size="sm"
              variant="ghost"
              disabled={account.is_live || archiveAccount.isPending}
              onClick={handleArchive}
              title={account.is_live ? "لا يمكن أرشفة حساب مباشر" : undefined}
            >
              <ArchiveIcon className="h-3.5 w-3.5" strokeWidth={1.75} />
              أرشفة
            </Button>
          </div>
        }
      />
      {archiveError ? (
        <p className="border-t border-border-soft bg-loss/5 px-4 py-2 text-xs text-loss-bright">
          {archiveError}
        </p>
      ) : null}
      {mode === "edit" ? <EditAccountForm account={account} onDone={() => setMode("view")} /> : null}
      {mode === "set-live" ? <SetLivePanel account={account} onDone={() => setMode("view")} /> : null}
    </Card>
  );
}

export default function AccountsPage() {
  const accountsQuery = useAccounts();
  const [showAdd, setShowAdd] = useState(false);

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-bone">حسابات MT5</h1>
          <p className="mt-1 text-sm text-muted">
            كل حساب معزول بالكامل — استراتيجيته وإعداداته الخاصة، لا يراه أحد غيرك. جميعها تستقبل
            من نفس قناة تيليكرام؛ حساب واحد فقط يكون &quot;مباشرًا&quot; فعليًا في كل لحظة.
          </p>
        </div>
        {!showAdd ? (
          <Button variant="primary" onClick={() => setShowAdd(true)}>
            <Plus className="h-4 w-4" strokeWidth={2} />
            حساب جديد
          </Button>
        ) : null}
      </div>

      {showAdd ? <AddAccountForm onDone={() => setShowAdd(false)} /> : null}

      {accountsQuery.isLoading ? (
        <LoadingState />
      ) : accountsQuery.isError || !accountsQuery.data ? (
        <ErrorState message="تعذّر تحميل قائمة الحسابات." />
      ) : accountsQuery.data.length === 0 ? (
        <EmptyState
          icon={Radio}
          title="لا توجد حسابات بعد"
          description="أضف أول حساب MT5 خاص بك لبدء استقبال الإشارات وتنفيذها."
        />
      ) : (
        <div className="flex flex-col gap-3">
          {accountsQuery.data.map((account) => (
            <AccountCard key={account.id} account={account} />
          ))}
        </div>
      )}
    </div>
  );
}
