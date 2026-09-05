export function formatNumber(value: string | number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "—";
  const n = typeof value === "string" ? Number(value) : value;
  if (Number.isNaN(n)) return "—";
  return n.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: digits });
}

export function formatMoney(value: string | number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "—";
  const n = typeof value === "string" ? Number(value) : value;
  if (Number.isNaN(n)) return "—";
  const sign = n > 0 ? "+" : "";
  return `${sign}$${n.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

export function formatPrice(value: string | number | null | undefined): string {
  return formatNumber(value, 2);
}

export function formatPercent(value: string | number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "—";
  const n = typeof value === "string" ? Number(value) : value;
  if (Number.isNaN(n)) return "—";
  return `${n.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: digits })}%`;
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("en-US", {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/** "قبل 3 دقائق" style relative time -- used for MT5Account.last_heartbeat_at. */
export function formatRelativeTime(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  const seconds = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
  if (seconds < 5) return "الآن";
  if (seconds < 60) return `قبل ${seconds} ثانية`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `قبل ${minutes} دقيقة`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `قبل ${hours} ساعة`;
  const days = Math.floor(hours / 24);
  return `قبل ${days} يوم`;
}

export function cn(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(" ");
}

const SIGNAL_STATUS_AR: Record<string, string> = {
  RECEIVED: "مستلمة",
  CLASSIFIED: "مصنّفة",
  WAITING_FOR_DETAILS: "بانتظار التفاصيل",
  READY: "جاهزة",
  VALIDATING: "قيد التحقق",
  REJECTED: "مرفوضة",
  EXECUTION_PENDING: "قيد التنفيذ",
  EXECUTED: "منفذة",
  FAILED: "فشلت",
  EXPIRED: "منتهية",
  IGNORED: "متجاهلة",
  DUPLICATE: "مكررة",
};

const TRADE_STATUS_AR: Record<string, string> = {
  CREATED: "أُنشئت",
  SUBMITTING: "قيد الإرسال",
  OPEN: "مفتوحة",
  CLOSED_TP: "مغلقة (هدف ربح)",
  CLOSED_SL: "مغلقة (وقف خسارة)",
  CLOSED_MANUAL: "مغلقة (يدويًا)",
  REJECTED: "مرفوضة",
  FAILED: "فشلت",
  UNKNOWN: "غير معروفة",
};

const RESULT_AR: Record<string, string> = {
  WIN: "ربح",
  LOSS: "خسارة",
  BREAKEVEN: "تعادل",
};

const SEVERITY_AR: Record<string, string> = {
  INFO: "معلومة",
  WARNING: "تحذير",
  ERROR: "خطأ",
  CRITICAL: "حرج",
};

const SIDE_AR: Record<string, string> = {
  BUY: "شراء",
  SELL: "بيع",
};

const SIGNAL_TYPE_AR: Record<string, string> = {
  MARKET: "سوق",
  LIMIT: "معلق",
  STOP: "إيقاف",
};

export function arSignalStatus(status: string): string {
  return SIGNAL_STATUS_AR[status] ?? status.replaceAll("_", " ");
}

export function arTradeStatus(status: string): string {
  return TRADE_STATUS_AR[status] ?? status.replaceAll("_", " ");
}

export function arResult(result: string): string {
  return RESULT_AR[result] ?? result;
}

export function arSeverity(severity: string): string {
  return SEVERITY_AR[severity] ?? severity;
}

export function arSide(side: string): string {
  return SIDE_AR[side] ?? side;
}

export function arSignalType(type: string): string {
  return SIGNAL_TYPE_AR[type] ?? type;
}

// Mirrors backend RejectionReason (app/core/constants.py). A FAILED trade
// carries the broker's own raw error text here instead of one of these
// codes, so arRejectionReason falls back to showing it verbatim -- an
// unreadable broker message still beats hiding why a trade never opened.
const REJECTION_REASON_AR: Record<string, string> = {
  AUTO_TRADING_DISABLED: "التداول التلقائي مطفأ",
  BUY_DISABLED: "إشارات الشراء مطفأة",
  SELL_DISABLED: "إشارات البيع مطفأة",
  STOP_LOSS_REQUIRED: "القناة لم ترسل وقف خسارة",
  STOP_LOSS_INVALID: "وقف خسارة غير صالح",
  STOP_LOSS_TOO_WIDE: "وقف الخسارة أبعد من الحد المسموح",
  DUPLICATE_SIGNAL: "إشارة مكرّرة — لها صفقة بالفعل",
  MAX_OPEN_TRADES_REACHED: "بلغت حد الصفقات المفتوحة",
  DAILY_TRADE_LIMIT_REACHED: "بلغت حد الصفقات اليومي",
  BROKER_UNAVAILABLE: "الوسيط غير متاح",
  INVALID_LOT_SIZE: "حجم اللوت غير صالح",
  INVALID_TARGET: "هدف النقاط غير صالح",
  UNSUPPORTED_SYMBOL: "رمز غير مدعوم",
  UNSUPPORTED_SIGNAL_TYPE: "نوع أمر غير مدعوم (معلّق)",
  SIGNAL_EXPIRED: "انتهت صلاحية الإشارة قبل وصول وقف الخسارة",
};

export function arRejectionReason(reason: string): string {
  return REJECTION_REASON_AR[reason] ?? reason;
}

const TRADING_MODE_AR: Record<string, string> = {
  SIMULATION: "محاكاة",
  DEMO: "تجريبي",
  LIVE: "حقيقي",
};

export function arTradingMode(mode: string): string {
  return TRADING_MODE_AR[mode] ?? mode;
}

const ARCHIVE_CLASSIFICATION_AR: Record<string, string> = {
  READY: "جاهزة للتنفيذ",
  NON_EXECUTABLE_TYPE: "نوع غير قابل للتنفيذ",
  NO_SL: "بدون وقف خسارة",
  AMBIGUOUS_OR_OTHER: "غامضة/أخرى",
};

const ARCHIVE_OUTCOME_AR: Record<string, string> = {
  WIN: "ربح",
  LOSS: "خسارة",
  UNRESOLVED: "لم تُحسم بعد",
  NO_PRICE_DATA: "لا توجد بيانات سعر",
};

export function arArchiveClassification(classification: string): string {
  return ARCHIVE_CLASSIFICATION_AR[classification] ?? classification;
}

export function arArchiveOutcome(outcome: string): string {
  return ARCHIVE_OUTCOME_AR[outcome] ?? outcome;
}
