import { clearAuthedMarker, getActiveAccountId } from "@/lib/account";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const accountId = getActiveAccountId();

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(accountId ? { "X-Account-Id": accountId } : {}),
      ...init?.headers,
    },
    // The session lives in an HttpOnly cookie set by the backend (Vercel
    // and the Cloudflare Tunnel are different HTTPS origins, so this is a
    // genuinely cross-site request) -- without this, the cookie is never
    // sent and every request looks logged-out regardless of a real login.
    credentials: "include",
    cache: "no-store",
  });

  if (!response.ok) {
    let detail: unknown;
    try {
      detail = await response.json();
    } catch {
      detail = await response.text().catch(() => undefined);
    }
    const message =
      (typeof detail === "object" && detail !== null && "detail" in detail
        ? String((detail as { detail: unknown }).detail)
        : undefined) ?? `فشل الطلب إلى ${path} برمز الحالة ${response.status}`;

    // A stale/revoked session anywhere but the login form itself (a wrong
    // password there is a normal, inline 401 the form displays -- not a
    // reason to bounce away from it) sends the user back to /login. The
    // proxy's cookie-presence check only guards page loads; an API 401
    // mid-session (expired token, logged out in another tab) needs this
    // instead. `window.location.href` (not useRouter()) deliberately --
    // this module is plain JS reachable outside any component's render,
    // and a hard navigation also clears every in-memory store (TanStack
    // Query cache included), which a stale session warrants anyway.
    if (response.status === 401 && typeof window !== "undefined" && path !== "/api/auth/login") {
      clearAuthedMarker();
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.href = "/login";
    }

    throw new ApiError(response.status, message, detail);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body: body ? JSON.stringify(body) : undefined }),
};

export { API_BASE_URL };
