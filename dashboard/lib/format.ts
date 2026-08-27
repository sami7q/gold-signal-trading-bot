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

const TRADING_MODE_AR: Record<string, string> = {
  SIMULATION: "محاكاة",
  DEMO: "تجريبي",
  LIVE: "حقيقي",
};

export function arTradingMode(mode: string): string {
  return TRADING_MODE_AR[mode] ?? mode;
}
