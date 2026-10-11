import { clearAuthedMarker } from "@/lib/account";

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
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
      // The session lives in an HttpOnly cookie set by the bot's API, which sits on a different site
      // (Cloudflare tunnel) from this dashboard (Vercel) -- without this the cookie is never sent.
      credentials: "include",
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "تعذّر الوصول إلى خادم البوت. تأكد أن البوت والنفق يعملان على السيرفر.");
  }

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

    // An expired/revoked session anywhere except the login form itself sends the user back to /login.
    // A hard navigation also clears every in-memory cache, which a stale session warrants anyway.
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
};

export { API_BASE_URL };
