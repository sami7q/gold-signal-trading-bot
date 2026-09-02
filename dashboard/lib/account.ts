// The currently-selected MT5 account, remembered across reloads (multi-account
// feature, 2026-09). Deliberately NOT HttpOnly -- lib/api.ts reads it on
// every request to set X-Account-Id, and the account switcher needs to read
// it too. It carries no secret; the actual access boundary is the session
// cookie + the backend's ownership check on X-Account-Id (see
// app/core/auth_deps.py::get_current_account), never this value alone.

const ACCOUNT_COOKIE = "goldbot_active_account_id";

// A same-origin marker `proxy.ts` can actually read (multi-account
// feature, 2026-09). The real session cookie is set by the backend, which
// lives on a *different* origin (Cloudflare Tunnel domain, not this
// dashboard's own Vercel domain) -- a cross-origin cookie is only ever
// visible to the browser's requests *to that origin*, never to this app's
// own Next.js server, so proxy.ts cannot read it directly no matter its
// SameSite/Secure flags. This marker carries no auth meaning by itself
// (it's plain, JS-writable, same-origin) -- it only avoids flashing the
// full dashboard shell before an inevitable client-side bounce to /login;
// real access control stays entirely server-side (app/core/auth_deps.py).
const AUTHED_MARKER_COOKIE = "goldbot_authed";

export function setAuthedMarker(): void {
  if (typeof document === "undefined") return;
  const oneYear = 60 * 60 * 24 * 365;
  document.cookie = `${AUTHED_MARKER_COOKIE}=1; path=/; max-age=${oneYear}; SameSite=Lax`;
}

export function clearAuthedMarker(): void {
  if (typeof document === "undefined") return;
  document.cookie = `${AUTHED_MARKER_COOKIE}=; path=/; max-age=0; SameSite=Lax`;
}

export { AUTHED_MARKER_COOKIE };

export function getActiveAccountId(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(new RegExp(`(?:^|; )${ACCOUNT_COOKIE}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

export function setActiveAccountId(accountId: number | string): void {
  if (typeof document === "undefined") return;
  const oneYear = 60 * 60 * 24 * 365;
  document.cookie = `${ACCOUNT_COOKIE}=${encodeURIComponent(String(accountId))}; path=/; max-age=${oneYear}; SameSite=Lax`;
}

export function clearActiveAccountId(): void {
  if (typeof document === "undefined") return;
  document.cookie = `${ACCOUNT_COOKIE}=; path=/; max-age=0; SameSite=Lax`;
}
