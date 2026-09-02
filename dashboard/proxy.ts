import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// UX-only gate (multi-account feature, 2026-09): the REAL session cookie
// is set by the backend, which lives on a different origin (Cloudflare
// Tunnel domain) than this dashboard (Vercel) -- a cross-origin cookie is
// never visible to this app's own Next.js server, so this proxy cannot
// read it directly, no matter its flags. It instead checks a same-origin
// marker cookie this app sets itself right after a successful login (see
// lib/account.ts's setAuthedMarker/clearAuthedMarker) -- carries no auth
// meaning on its own, just avoids flashing the full dashboard shell before
// an inevitable client-side bounce to /login. Real access control is
// entirely server-side (app/core/auth_deps.py); an expired/revoked session
// found mid-session (marker present, backend session actually invalid) is
// caught by lib/api.ts's 401 handler instead.
const AUTHED_MARKER_COOKIE = "goldbot_authed";

export function proxy(request: NextRequest) {
  const hasMarker = request.cookies.has(AUTHED_MARKER_COOKIE);
  const isLoginPage = request.nextUrl.pathname === "/login";

  if (!hasMarker && !isLoginPage) {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("next", request.nextUrl.pathname);
    return NextResponse.redirect(loginUrl);
  }

  if (hasMarker && isLoginPage) {
    return NextResponse.redirect(new URL("/", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    // Everything except static assets / Next internals / the favicon.
    "/((?!_next/static|_next/image|favicon.ico).*)",
  ],
};
