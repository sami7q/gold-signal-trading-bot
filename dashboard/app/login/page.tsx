"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Lock, User } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { useLogin } from "@/hooks/use-auth";
import { ApiError } from "@/lib/api";

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const login = useLogin();

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [rememberMe, setRememberMe] = useState(true);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    login.mutate(
      { username, password, remember_me: rememberMe },
      {
        onSuccess: () => {
          const next = searchParams.get("next");
          router.replace(next && next !== "/login" ? next : "/");
          router.refresh();
        },
      },
    );
  }

  const errorMessage =
    login.error instanceof ApiError ? login.error.message : login.error ? "تعذّر تسجيل الدخول." : null;

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center gap-1.5 text-center">
          <span className="text-2xl font-bold text-gold-bright">بوت الفلوووووس</span>
          <span className="text-sm font-medium text-muted">نسخ صفقات المتداولين</span>
        </div>

        <form
          onSubmit={handleSubmit}
          className="rounded-md border border-border bg-surface/80 p-6 backdrop-blur-sm"
        >
          <h1 className="mb-5 text-center text-base font-semibold text-bone">تسجيل الدخول</h1>

          <div className="mb-4">
            <Label htmlFor="username">اسم المستخدم</Label>
            <div className="relative">
              <User
                className="pointer-events-none absolute right-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-dim"
                strokeWidth={1.75}
              />
              <Input
                id="username"
                autoFocus
                autoComplete="username"
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="pr-8"
              />
            </div>
          </div>

          <div className="mb-4">
            <Label htmlFor="password">كلمة المرور</Label>
            <div className="relative">
              <Lock
                className="pointer-events-none absolute right-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-dim"
                strokeWidth={1.75}
              />
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="pr-8"
              />
            </div>
          </div>

          <label className="mb-5 flex items-center gap-2 text-sm text-muted">
            <input
              type="checkbox"
              checked={rememberMe}
              onChange={(e) => setRememberMe(e.target.checked)}
              className="h-3.5 w-3.5 rounded border-border bg-ink accent-gold"
            />
            تذكّرني
          </label>

          {errorMessage ? (
            <p className="mb-4 rounded border border-loss/40 bg-loss/10 px-2.5 py-2 text-xs text-loss-bright">
              {errorMessage}
            </p>
          ) : null}

          <Button
            type="submit"
            variant="primary"
            className="w-full justify-center"
            disabled={login.isPending}
          >
            {login.isPending ? "جارٍ الدخول…" : "دخول"}
          </Button>
        </form>
      </div>
    </div>
  );
}
