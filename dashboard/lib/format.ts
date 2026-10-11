export function cn(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(" ");
}

function toNumber(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const n = typeof value === "string" ? Number(value) : value;
  return Number.isNaN(n) ? null : n;
}

export function formatNumber(value: string | number | null | undefined, digits = 2): string {
  const n = toNumber(value);
  if (n === null) return "—";
  return n.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: digits });
}

/** Prices of very different magnitudes (BTC ~80,000 vs kPEPE ~0.009): keep ~6 significant digits. */
export function formatPrice(value: string | number | null | undefined): string {
  const n = toNumber(value);
  if (n === null) return "—";
  const abs = Math.abs(n);
  const digits = abs >= 1000 ? 2 : abs >= 1 ? 4 : 8;
  return n.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: digits });
}

export function formatMoney(value: string | number | null | undefined, digits = 2): string {
  const n = toNumber(value);
  if (n === null) return "—";
  const sign = n > 0 ? "+" : n < 0 ? "-" : "";
  return `${sign}$${Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

export function formatUsd(value: string | number | null | undefined): string {
  const n = toNumber(value);
  if (n === null) return "—";
  return `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function formatPercent(value: string | number | null | undefined, digits = 1): string {
  const n = toNumber(value);
  if (n === null) return "—";
  return `${n.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: digits })}%`;
}

export function formatMs(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value >= 10_000) return `${(value / 1000).toFixed(1)} ث`;
  return `${Math.round(value).toLocaleString("en-US")} ms`;
}

export function pnlTone(value: string | number | null | undefined): "win" | "loss" | "default" {
  const n = toNumber(value) ?? 0;
  return n > 0 ? "win" : n < 0 ? "loss" : "default";
}

export function pnlClass(value: string | number | null | undefined): string {
  const tone = pnlTone(value);
  return tone === "win" ? "text-win-bright" : tone === "loss" ? "text-loss-bright" : "text-muted";
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("en-US", { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
}

export function formatRelativeTime(value: string | null | undefined): string {
  if (!value) return "أبدًا";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  const seconds = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
  if (seconds < 5) return "الآن";
  if (seconds < 60) return `قبل ${seconds} ثانية`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `قبل ${minutes} دقيقة`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `قبل ${hours} ساعة`;
  return `قبل ${Math.floor(hours / 24)} يوم`;
}

const COPY_STATUS_AR: Record<string, string> = {
  OBSERVED_ONLY: "مراقبة فقط",
  SUBMITTING: "قيد الإرسال",
  OPEN: "مفتوحة",
  CLOSED_LEADER: "أُغلقت مع Loco",
  CLOSED_TP: "هدف الربح",
  CLOSED_LIQ: "تصفية",
  CLOSED_MANUAL: "إغلاق يدوي",
  CLOSED_RECONCILED: "إغلاق تصحيحي",
  FAILED: "فشلت",
  UNKNOWN: "غير معروفة",
};

const DECISION_AR: Record<string, string> = {
  COPIED: "نُسخت",
  FOLLOWED: "تمت المتابعة",
  OBSERVED_ONLY: "مراقبة فقط",
  REJECTED: "مرفوضة",
  IGNORED_ADD: "تعزيز — متجاهل",
  IGNORED_REDUCE: "تقليص — متجاهل",
  NO_MATCHING_COPY: "لا صفقة مقابلة",
  FAILED: "فشل",
};

const EVENT_TYPE_AR: Record<string, string> = {
  OPEN: "فتح",
  ADD: "تعزيز",
  REDUCE: "تقليص",
  CLOSE: "إغلاق",
  FLIP: "انعكاس",
};

// Mirrors loco_copy/constants.py::Rejection.
const REJECTION_AR: Record<string, string> = {
  DISABLED: "النسخ متوقف (وضع المراقبة)",
  KILL_SWITCH: "الإيقاف الطارئ مفعّل",
  DAILY_LOSS_LIMIT: "بلغ حد الخسارة اليومي",
  LEADER_BREAKER: "قاطع تراجع Loco مفعّل",
  LATENCY_BREAKER: "الاتصال بـ Loco متأخر أو منقطع",
  UNSUPPORTED_MARKET: "سوق غير مدعوم",
  PRE_EXISTING_POSITION: "مركز سابق لـ Loco — لا يُنسخ",
  SIDE_DISABLED: "هذا الاتجاه معطّل",
  COIN_BLOCKED: "عملة محظورة",
  SYMBOL_NOT_LISTED: "العملة غير مدرجة في Bybit",
  SIGNAL_TOO_OLD: "الإشارة قديمة",
  PRICE_DRIFTED: "السعر ابتعد كثيرًا عن سعر Loco",
  DUPLICATE_EPISODE: "مكررة",
  COIN_ALREADY_OPEN: "يوجد مركز مفتوح لنفس العملة",
  BROKER_UNAVAILABLE: "الوسيط غير متاح",
  MAX_OPEN_POSITIONS: "بلغ الحد الأقصى للمراكز",
  INSUFFICIENT_MARGIN: "الرصيد المتاح غير كافٍ",
  NOTIONAL_CAP: "تجاوز حد الحجم",
  QTY_TOO_SMALL: "الكمية أصغر من الحد الأدنى",
};

const MODE_AR: Record<string, string> = {
  WATCH: "مراقبة فقط",
  OBSERVE: "محاكاة بدون تنفيذ",
  EXECUTE: "تنفيذ فعلي",
};

const SEVERITY_AR: Record<string, string> = {
  INFO: "معلومة",
  WARNING: "تحذير",
  ERROR: "خطأ",
  CRITICAL: "حرج",
};

export const arCopyStatus = (s: string) => COPY_STATUS_AR[s] ?? s;
export const arDecision = (s: string) => DECISION_AR[s] ?? s;
export const arEventType = (s: string) => EVENT_TYPE_AR[s] ?? s;
export const arRejection = (s: string) => REJECTION_AR[s] ?? s;
export const arMode = (s: string) => MODE_AR[s] ?? s;
export const arSeverity = (s: string) => SEVERITY_AR[s] ?? s;
export const arSide = (s: string | null) => (s === "LONG" ? "شراء" : s === "SHORT" ? "بيع" : "—");

export function brokerLabel(broker: string | undefined, env: string | undefined): string {
  if (!broker) return "—";
  if (broker === "paper") return "تداول ورقي";
  return env === "live" ? "Bybit حقيقي" : "Bybit تجريبي";
}

/** One readable line out of a log payload: `coin=HYPE reason=PRICE_DRIFTED`. */
export function payloadSummary(payload: Record<string, unknown>): string {
  return Object.entries(payload)
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join("  ");
}
