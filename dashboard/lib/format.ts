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

/** 16:20:15.366 -- to the millisecond, for the step-by-step timeline of a trade. */
export function formatClock(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  const hms = d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
  return `${hms}.${String(d.getMilliseconds()).padStart(3, "0")}`;
}

/** 0.4 ث · 22 د 32 ث · 3 س 5 د */
export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(s < 10 ? 2 : 1)} ث`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} د ${Math.round(s % 60)} ث`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} س ${m % 60} د`;
  return `${Math.floor(h / 24)} يوم ${h % 24} س`;
}

/** Signed basis points; positive means worse for us than Loco. */
export function formatBps(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)} bps`;
}

export function formatSignedPercent(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}%`;
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
  CLOSED_LEADER: "أُغلقت مع المتداول",
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
  ADDED: "تعزيز منسوخ",
  ADD_PENDING: "تعزيز مؤجَّل (أقل من الحد الأدنى)",
  ADD_CAPPED: "بلغت السقف — تعزيز متجاهل",
  REDUCED: "قُلّصت مع المتداول",
  CLOSED: "أُغلقت مع المتداول",
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
  LEADER_BREAKER: "قاطع تراجع المتداول مفعّل",
  LANE_PAUSED: "المتداول موقوف من صفحة المتداولين",
  COIN_HELD_BY_OTHER_LEADER: "العملة مفتوحة لمتداول آخر على نفس الحساب",
  EXECUTION_NOT_ALLOWED: "التنفيذ الفعلي مقفل لهذا المتداول — سُجّلت محاكاة",
  LATENCY_BREAKER: "الاتصال بـ Hyperliquid متأخر أو منقطع",
  UNSUPPORTED_MARKET: "سوق غير مدعوم",
  PRE_EXISTING_POSITION: "مركز سابق للمتداول — لا يُنسخ",
  SIDE_DISABLED: "هذا الاتجاه معطّل",
  COIN_BLOCKED: "عملة محظورة",
  SYMBOL_NOT_LISTED: "العملة غير مدرجة في Bybit",
  SIGNAL_TOO_OLD: "الإشارة قديمة",
  PRICE_DRIFTED: "السعر ابتعد كثيرًا عن سعر المتداول",
  DUPLICATE_EPISODE: "مكررة",
  COIN_ALREADY_OPEN: "يوجد مركز مفتوح لنفس العملة",
  BROKER_UNAVAILABLE: "الوسيط غير متاح",
  MAX_OPEN_POSITIONS: "بلغ الحد الأقصى للمراكز",
  INSUFFICIENT_MARGIN: "الرصيد المتاح غير كافٍ",
  NOTIONAL_CAP: "تجاوز حد الحجم",
  QTY_TOO_SMALL: "الكمية أصغر من الحد الأدنى",
  FEED_OUTAGE: "انقطاع Hyperliquid أكثر من دقيقتين — لا صفقات أو تعزيزات جديدة",
  EXPOSURE_LIMIT: "بلغ الحجم الكلي حدّه (6 أضعاف الرصيد)",
  ACCOUNT_KILL: "قاطع الحساب مفعّل: الرصيد نزل تحت 75% من قمته",
  LEADER_EQUITY_UNKNOWN: "رصيد المتداول غير معروف — لا يمكن حساب الحجم",
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

/** What a trade's final state means, in one sentence the owner can act on. */
const COPY_STATUS_EXPLAIN: Record<string, string> = {
  OBSERVED_ONLY:
    "صفقة افتراضية من وضع المحاكاة — لم يُرسل أي أمر للوسيط. في استراتيجية السقف تُسجَّل على الحساب الافتراضي بسعر Bybit الحي لحظتها.",
  SUBMITTING: "الأمر قيد الإرسال للوسيط.",
  OPEN: "مفتوحة الآن على الوسيط، وتنتظر إغلاق المتداول أو وصول هدف الربح.",
  CLOSED_LEADER: "أغلق المتداول مركزه، فأغلق البوت صفقتنا معه مباشرة.",
  CLOSED_TP: "وصل السعر إلى هدف الربح، فأغلقها الوسيط تلقائيًا قبل أن يغلق المتداول.",
  CLOSED_LIQ: "تحرك السعر ضدنا حتى سعر التصفية، فصفّاها الوسيط.",
  CLOSED_MANUAL: "أُغلقت من خارج البوت — يدويًا من حساب الوسيط.",
  CLOSED_RECONCILED:
    "أغلق المتداول مركزه لكن البوت لم يلتقط الإغلاق لحظتها، فأغلقها نظام الحماية تلقائيًا بعد أن تأكد أنه لم يعد في الصفقة.",
  FAILED: "لم تُفتح الصفقة: رفض الوسيط الأمر أو فشل الإرسال.",
  UNKNOWN: "اختفى المركز من الوسيط بدون سجل إغلاق واضح — راجع حساب الوسيط.",
};

const ORDER_REASON_AR: Record<string, string> = {
  open: "فتح مع المتداول",
  close: "إغلاق مع المتداول",
  reduce: "تقليص مع المتداول",
  add: "تعزيز مع المتداول",
  reconcile: "تصحيح الكمية (المطابقة)",
  reconciled: "إغلاق تصحيحي (نظام الحماية)",
  kill: "إيقاف طارئ",
  close_all: "إيقاف + إغلاق الكل",
  manual: "يدوي",
};

const ORDER_KIND_AR: Record<string, string> = { OPEN: "فتح", REDUCE: "تقليص", CLOSE: "إغلاق" };

// The log types that show up in a trade's timeline (warnings and above about this coin).
const LOG_TYPE_AR: Record<string, string> = {
  MISSED_EXIT_AUTO_CLOSED: "لم يُلتقط إغلاق المتداول — أُغلقت تلقائيًا",
  LEADER_STATE_DRIFT: "اختلف حساب البوت لمركز المتداول عن الحقيقة",
  TP_UPDATE_FAILED: "تعذّر تعديل هدف الربح",
  FORCE_CLOSE_FAILED: "فشل الإغلاق الإجباري",
  POSITION_MISMATCH: "كمية المركز على الوسيط تختلف عن المسجّلة",
  ORPHAN_POSITION: "مركز على الوسيط لا يعرفه البوت",
  OPEN_FAILED: "فشل فتح الصفقة",
  OPEN_REJECTED: "رُفضت الصفقة",
  COPY_CLOSED_LIQ: "تمت تصفية الصفقة",
  COPY_CLOSED_MANUAL: "أُغلقت يدويًا",
  COPY_UNKNOWN: "المركز اختفى بدون سجل",
  CLOSED_PNL_UNAVAILABLE: "تعذّر جلب ربح الإغلاق من الوسيط",
  CAP20_ORDER_FAILED: "فشل أمر فتح أو تعزيز",
  EXIT_FAILED: "فشل أمر الخروج",
  ORDER_REJECTED_RETRY: "رُفض الأمر — أُعيدت المحاولة مرة",
  RECONCILE_CORRECTED: "صُحّحت الكمية لتطابق المتداول",
  CROSS_LEVERAGE_FAILED: "تعذّر ضبط الهامش المشترك والرافعة",
  EXPOSURE_LIMIT: "بلغ الحجم الكلي حدّه",
  ACCOUNT_KILL_TRIPPED: "قاطع الحساب: الرصيد نزل تحت 75% من قمته",
  ACCOUNT_LIQUIDATED: "تصفية الحساب (المشترك)",
  DAILY_SUMMARY: "الملخص اليومي",
  SKIPPED_PREEXISTING: "حركة على مركز قديم للمتداول — متجاهلة حتى يغلقه",
  RECONCILE_STALE_SNAPSHOT: "لقطة المتداول قديمة — لا تصحيح للكميات",
  STRATEGY_WARNING: "إعداد خارج المواصفة",
};

/** "Loco" -- other leaders have no public name, only the internal label they were given in LEADERS. */
export function leaderName(label: string | null | undefined): string {
  if (!label) return "—";
  return label === "loco" ? "Loco" : label;
}

/** Long form for headings: says that a label is internal, so nobody mistakes it for the trader's real name. */
export function leaderTitle(label: string): string {
  return label === "loco" ? "Loco" : `${label} (لقب داخلي — لا يوجد اسم علني)`;
}

export function shortAddress(address: string | null | undefined): string {
  if (!address) return "—";
  return `${address.slice(0, 6)}…${address.slice(-4)}`;
}

export const arCopyStatus = (s: string) => COPY_STATUS_AR[s] ?? s;
export const arCopyStatusExplain = (s: string) => COPY_STATUS_EXPLAIN[s] ?? "";
export const arOrderReason = (s: string) => ORDER_REASON_AR[s] ?? s;
export const arOrderKind = (s: string) => ORDER_KIND_AR[s] ?? s;
export const arLogType = (s: string) => LOG_TYPE_AR[s] ?? s;
export const arDecision = (s: string) => DECISION_AR[s] ?? s;
export const arEventType = (s: string) => EVENT_TYPE_AR[s] ?? s;
export const arRejection = (s: string) => REJECTION_AR[s] ?? s;
export const arMode = (s: string) => MODE_AR[s] ?? s;
export const arSeverity = (s: string) => SEVERITY_AR[s] ?? s;
/** cap20: the leader's position sizes and our ratio, compact. */
export function formatRatio(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return n >= 0.01 ? n.toFixed(4) : n.toPrecision(3);
}

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
