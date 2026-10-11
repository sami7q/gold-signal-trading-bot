// A same-origin marker that proxy.ts can actually read. The real session cookie is set by the bot's API on
// a *different* site (the Cloudflare tunnel), which this app's own Next.js server can never see, so proxy.ts
// checks this marker instead. It carries no auth meaning by itself -- it only avoids flashing the dashboard
// before an inevitable bounce to /login; real access control is entirely server-side (loco_copy/web.py).
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
