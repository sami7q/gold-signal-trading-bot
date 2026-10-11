// Shapes returned by the loco-copy dashboard API (loco_copy/web.py). Decimals arrive as strings so no
// precision is lost to JSON floats; timestamps are ISO-8601 UTC strings.

export type Side = "LONG" | "SHORT";
export type Mode = "WATCH" | "OBSERVE" | "EXECUTE";

export type CopyStatus =
  | "OBSERVED_ONLY"
  | "SUBMITTING"
  | "OPEN"
  | "CLOSED_LEADER"
  | "CLOSED_TP"
  | "CLOSED_LIQ"
  | "CLOSED_MANUAL"
  | "CLOSED_RECONCILED"
  | "FAILED"
  | "UNKNOWN";

export type Decision =
  | "COPIED"
  | "FOLLOWED"
  | "OBSERVED_ONLY"
  | "REJECTED"
  | "IGNORED_ADD"
  | "IGNORED_REDUCE"
  | "NO_MATCHING_COPY"
  | "FAILED";

export type EventType = "OPEN" | "ADD" | "REDUCE" | "CLOSE" | "FLIP";

export interface User {
  id: number;
  username: string;
  display_name: string;
}

export interface MeResponse {
  user: User;
}

export interface LoginRequest {
  username: string;
  password: string;
  remember_me?: boolean;
}

export interface CopyTrade {
  id: number;
  coin: string;
  symbol: string;
  episode_id: number;
  side: Side;
  status: CopyStatus;
  observed: boolean;
  qty_initial: string;
  qty_open: string;
  leader_entry_px: string;
  entry_price: string | null;
  tp_price: string | null;
  exit_price_avg: string | null;
  realized_pnl: string;
  fees: string;
  margin: string | null;
  opened_at: string | null;
  closed_at: string | null;
  close_reason: string | null;
  slippage_bps: string | null;
  latency_detect_ms: number | null;
  latency_order_ms: number | null;
  /** Label of the copied leader (lane): "loco", "3ea" ... */
  leader: string;
}

export interface Metrics {
  leader_opens: number;
  copied_opens: number;
  not_listed: number;
  capture_rate: number | null;
  detect_p50_ms: number | null;
  detect_p95_ms: number | null;
  order_p50_ms: number | null;
  slippage_median_bps: number | null;
  missed_exits: number;
  liquidations: number;
  take_profits: number;
  leader_exits: number;
  realized_pnl: string;
  open_copies: number;
  rejections: Record<string, number>;
}

export interface Overview {
  bot: {
    running: boolean;
    heartbeat_at: string | null;
    ws_connected: boolean;
    last_frame_at: string | null;
    last_snapshot_at: string | null;
    snapshot_failures: number;
    ws_reconnects: number;
  };
  account: { broker: "paper" | "bybit"; env: "demo" | "live"; equity: string | null; available: string | null };
  mode: { mode: Mode; live: boolean };
  lanes: Array<{ label: string; name: string; mode: Mode; paused: boolean }>;
  risk: { kill_switch: boolean; stop_file: boolean; leader_breaker: boolean; daily_loss_hit: boolean };
  leader: { label: string; name: string; address: string; account_value: string | null; snapshot_at: string | null; open_positions: number };
  stats: {
    open_copies: number;
    trades_total: number;
    closed: number;
    wins: number;
    losses: number;
    win_rate: number | null;
    realized_pnl_total: string;
    realized_pnl_today: string;
    closed_today: number;
    take_profits: number;
    liquidations: number;
    observed_total: number;
    observed_pnl: string;
  };
  metrics_24h: Metrics;
}

export interface LocoRow {
  coin: string;
  side: Side | null;
  size: string;
  entry_px: string | null;
  copyable: boolean;
  ours: CopyTrade | null;
}

export interface LocoPositions {
  label: string;
  name: string;
  address: string;
  snapshot_at: string | null;
  account_value: string | null;
  rows: LocoRow[];
}

export interface LeaderEvent {
  id: number;
  coin: string;
  episode_id: number;
  type: EventType;
  side: Side | null;
  fraction: string | null;
  leader_px: string;
  leader_time: string;
  detected_at: string;
  lag_ms: number;
  decision: Decision;
  rejection: string | null;
  detail: string | null;
  /** Our trade for this Loco episode, if there is one. */
  copy_id: number | null;
  copy_status: CopyStatus | null;
  leader: string;
}

/** One of Loco's own fills (Hyperliquid). */
export interface LeaderFillInfo {
  tid: number;
  oid: number | null;
  side: "BUY" | "SELL";
  px: string;
  sz: string;
  start_position: string;
  new_position: string;
  time: string;
  received_at: string;
}

/** A whole Loco order: Hyperliquid can fill one order in dozens of pieces. */
export interface LeaderOrder {
  fills: number;
  side: "BUY" | "SELL";
  size: string;
  avg_px: string | null;
  notional: string;
  position_before: string;
  position_after: string;
  first_at: string;
  last_at: string;
}

export interface OrderInfo {
  id: number;
  kind: "OPEN" | "REDUCE" | "CLOSE";
  /** Why it was sent: open / close / reduce / reconciled / kill … */
  reason: string;
  client_id: string;
  requested_qty: string;
  filled_qty: string | null;
  avg_price: string | null;
  fee: string | null;
  status: string;
  error: string | null;
  sent_at: string | null;
  filled_at: string | null;
  latency_ms: number | null;
}

export type TimelineItem = { at: string; at_ms: number } & (
  | { kind: "LEADER_OPEN"; px: string; order: LeaderOrder | null }
  | { kind: "DETECTED"; lag_ms: number }
  | { kind: "ORDER_SENT" | "ORDER_FILLED" | "ORDER_FAILED"; order: OrderInfo }
  | { kind: "TP_SET"; px: string; move_pct: number | null }
  | { kind: "LEADER_ADD" | "LEADER_REDUCE" | "LEADER_CLOSE" | "LEADER_FLIP"; event: LeaderEvent }
  | { kind: "LEADER_EXIT_MISSED"; order: LeaderOrder | null }
  | { kind: "LOG"; level: LogEntry["level"]; type: string; payload: Record<string, unknown> }
  | { kind: "CLOSED"; status: CopyStatus; reason: string | null }
);

export interface CopyDetail {
  copy: CopyTrade & { exit_qty_total: string; funding: string };
  figures: {
    notional: string | null;
    leverage: number;
    margin: string | null;
    gross_pnl: string | null;
    fees: string;
    funding: string;
    net_pnl: string;
    roi_pct: number | null;
    price_move_pct: number | null;
    tp_move_pct: number | null;
    liq_price_est: string | null;
    duration_ms: number | null;
  };
  /** Positive bps = worse for us than Loco. */
  comparison: { entry_diff_bps: number | null; exit_diff_bps: number | null; exit_delay_ms: number | null };
  leader: { open: LeaderOrder | null; exit: LeaderOrder | null; exit_captured: boolean; exit_time: string | null };
  orders: OrderInfo[];
  timeline: TimelineItem[];
}

export interface EventDetail {
  event: LeaderEvent;
  fill: LeaderFillInfo | null;
  leader_order: LeaderOrder | null;
  copy: CopyTrade | null;
}

export interface LogEntry {
  id: number;
  ts: string;
  level: "INFO" | "WARNING" | "ERROR" | "CRITICAL";
  type: string;
  payload: Record<string, unknown>;
}

export interface Analytics {
  days: number;
  curve: Array<{ closed_at: string; coin: string; leader: string; pnl: string; cumulative: string }>;
  daily: Array<{ day: string; trades: number; wins: number; losses: number; pnl: string }>;
  by_close: Record<string, number>;
  metrics: Metrics;
}

export interface SettingsView {
  leader_address: string;
  broker: "paper" | "bybit";
  broker_env: "demo" | "live";
  live: boolean;
  mode: Mode;
  leaders: Array<{ label: string; name: string; address: string; mode: Mode; allow_execute: boolean }>;
  strategy: {
    risk_per_trade: string;
    leverage: number;
    take_profit_roi: string;
    take_profit_move_pct: string;
    liquidation_move_pct: string;
    follow_adds: boolean;
    follow_reduces: boolean;
    max_open_positions: number;
    max_notional_per_coin: string | null;
    allow_long: boolean;
    allow_short: boolean;
    coin_allowlist: string[];
    coin_blocklist: string[];
  };
  safety: {
    max_entry_drift_pct: string;
    max_signal_age_ms: number;
    max_order_slippage_pct: string;
    daily_loss_limit_pct: string;
    leader_dd_breaker_pct: string;
    missed_exit_seconds: number;
  };
}

/** One copied leader (a "lane") as the traders page shows it -- `GET /api/leaders`. */
export interface LeaderSummary {
  label: string;
  /** "Loco" -- or the internal label for a leader without a public name. */
  name: string;
  address: string;
  internal_label: boolean;
  mode: Mode;
  /** The server-side execution gate (ALLOW_EXECUTE__<LABEL>): EXECUTE is refused while false. */
  allow_execute: boolean;
  /** The on/off switch: paused = no new copies of this leader (his exits are still followed). */
  paused: boolean;
  breaker: boolean;
  account: string | null;
  shared_account: boolean;
  equity: string | null;
  available: string | null;
  snapshot_at: string | null;
  account_value: string | null;
  leader_positions: number;
  open_copies: number;
  observed_open: number;
  trades_total: number;
  closed: number;
  wins: number;
  losses: number;
  realized_pnl: string;
  observed_pnl: string;
  last_event_at: string | null;
  strategy: { risk_per_trade: string; leverage: number; take_profit_move_pct: string; max_open_positions: number };
}
