export type TradingMode = "SIMULATION" | "DEMO" | "LIVE";
export type Side = "BUY" | "SELL";
export type OrderType = "MARKET" | "LIMIT" | "STOP";

export type SignalStatus =
  | "RECEIVED"
  | "CLASSIFIED"
  | "WAITING_FOR_DETAILS"
  | "READY"
  | "VALIDATING"
  | "REJECTED"
  | "EXECUTION_PENDING"
  | "EXECUTED"
  | "FAILED"
  | "EXPIRED"
  | "IGNORED"
  | "DUPLICATE";

export type TradeStatus =
  | "CREATED"
  | "SUBMITTING"
  | "OPEN"
  | "CLOSED_TP"
  | "CLOSED_SL"
  | "CLOSED_MANUAL"
  | "REJECTED"
  | "FAILED"
  | "UNKNOWN";

export type TradeResult = "WIN" | "LOSS" | "BREAKEVEN";

export type AuditSeverity = "INFO" | "WARNING" | "ERROR" | "CRITICAL";

export interface TradingSettings {
  id: number;
  auto_trading: boolean;
  mode: TradingMode;
  buy_enabled: boolean;
  sell_enabled: boolean;
  lot_size: string;
  target_pips: string;
  require_stop_loss: boolean;
  max_open_trades: number;
  max_channel_stop_pips: string | null;
  signal_completion_timeout_seconds: number;
  auto_sl_fallback_pips: string | null;
  daily_lot_plan_enabled: boolean;
  martingale_lot_multiplier: string;
  monday_first_trade_lot: string;
  monday_second_trade_lot: string;
  max_trades_per_day: number;
}

export interface PipPreview {
  symbol: string;
  broker: string;
  broker_healthy: boolean;
  digits: number | null;
  point: string | null;
  pip_size: string | null;
  target_pips: string;
  price_distance: string | null;
  example_entry: string | null;
  calculated_take_profit: string | null;
}

export interface TradingSettingsUpdate {
  auto_trading?: boolean;
  mode?: TradingMode;
  buy_enabled?: boolean;
  sell_enabled?: boolean;
  lot_size?: string;
  target_pips?: string;
  require_stop_loss?: boolean;
  max_open_trades?: number;
  max_channel_stop_pips?: string | null;
  signal_completion_timeout_seconds?: number;
  auto_sl_fallback_pips?: string | null;
  daily_lot_plan_enabled?: boolean;
  martingale_lot_multiplier?: string;
  monday_first_trade_lot?: string;
  monday_second_trade_lot?: string;
  max_trades_per_day?: number;
  confirm_live_phrase?: string;
}

export interface Trade {
  id: number;
  signal_id: number;
  account_id: number;
  execution_key: string;
  broker: string;
  broker_order_id: string | null;
  broker_position_id: string | null;
  symbol: string;
  side: Side;
  requested_lot: string;
  executed_lot: string | null;
  entry_price: string | null;
  stop_loss: string | null;
  target_pips: string;
  take_profit_price: string | null;
  opened_at: string | null;
  closed_at: string | null;
  exit_price: string | null;
  profit: string | null;
  profit_currency: string;
  status: TradeStatus;
  result: TradeResult | null;
  close_reason: string | null;
  failure_reason: string | null;
  created_at: string;
  updated_at: string;
}

export type MessageRole = "ENTRY" | "DETAILS" | "STOP_LOSS" | "TARGETS" | "UPDATE" | "RESULT" | "OTHER";

export interface SignalSourceMessage {
  role: MessageRole;
  raw_text: string;
  normalized_text: string;
  sent_at: string;
}

export interface Signal {
  id: number;
  symbol: string;
  side: Side;
  signal_type: OrderType;
  entry_min: string | null;
  entry_max: string | null;
  channel_stop_loss: string | null;
  status: SignalStatus;
  source: string;
  channel_id: string;
  first_message_at: string;
  completed_at: string | null;
  expires_at: string | null;
  rejection_reason: string | null;
  created_at: string;
  updated_at: string;
  linked_trade_id: number | null;
  messages: SignalSourceMessage[];
}

export interface AuditEvent {
  id: number;
  event_type: string;
  severity: AuditSeverity;
  message: string;
  metadata_json: string;
  created_at: string;
}

export interface TradeStatistics {
  trades_executed: number;
  open_trades: number;
  closed_trades: number;
  wins: number;
  losses: number;
  win_rate: string;
  net_profit: string;
  gross_profit: string;
  gross_loss: string;
  average_win: string;
  average_loss: string;
  best_trade: string | null;
  worst_trade: string | null;
  today_trades: number;
  today_pl: string;
}

export interface DashboardSummary {
  total_signals_received: number;
  executable_signals: number;
  ignored_messages: number;
  rejected_signals: number;
  statistics: TradeStatistics;
  balance: string | null;
  equity: string | null;
  broker_healthy: boolean;
}

export interface DashboardSummaryResponse {
  summary: DashboardSummary;
  settings: TradingSettings;
}

export interface ProfitCurvePoint {
  closed_at: string;
  trade_id: number;
  profit: string;
  cumulative_profit: string;
}

export interface DailyPerformance {
  day: string;
  trades: number;
  wins: number;
  losses: number;
  profit: string;
}

export interface OrderPositionPerformance {
  position: number;
  trades: number;
  wins: number;
  losses: number;
  win_rate: string;
}

export type ArchiveClassification = "READY" | "NON_EXECUTABLE_TYPE" | "NO_SL" | "AMBIGUOUS_OR_OTHER";
export type ArchiveOutcome = "WIN" | "LOSS" | "UNRESOLVED" | "NO_PRICE_DATA";

export interface ArchiveSummary {
  total_signals: number;
  ready_signals: number;
  non_executable_signals: number;
  no_sl_signals: number;
  ambiguous_or_other_signals: number;
  wins: number;
  losses: number;
  unresolved: number;
  no_price_data: number;
  win_rate: string;
  net_profit_estimate: string;
  gross_profit_estimate: string;
  gross_loss_estimate: string;
}

export interface ArchivedSignal {
  id: number;
  channel_id: string;
  message_id: string;
  symbol: string;
  side: string;
  signal_type: string;
  entry_min: string | null;
  entry_max: string | null;
  channel_stop_loss: string | null;
  raw_entry_text: string;
  raw_sl_text: string | null;
  signal_posted_at: string;
  sl_posted_at: string | null;
  classification: ArchiveClassification;
  simulated_entry_price: string | null;
  simulated_take_profit_price: string | null;
  outcome: ArchiveOutcome | null;
  outcome_price: string | null;
  outcome_at: string | null;
  profit_estimate: string | null;
}

export interface HealthStatus {
  healthy: boolean;
  detail: string;
}

export interface SignalSourceHealth extends HealthStatus {
  source: string;
  enabled: boolean;
  connected: boolean;
  authenticated: boolean;
  allowed_channel_configured: boolean;
  last_message_at: string | null;
  last_error: string | null;
}

export interface SimulationPosition {
  position_id: string;
  symbol: string;
  side: string;
  lot: string;
  entry_price: string;
  stop_loss: string | null;
  take_profit: string | null;
  is_open: boolean;
  close_price: string | null;
  profit: string | null;
  close_reason: string | null;
}

export interface SimulationState {
  symbol: string;
  bid: string;
  ask: string;
  balance: string;
  equity: string;
  open_positions: SimulationPosition[];
}

export interface SendMessageResult {
  message_id: string;
  duplicate: boolean;
  classification: string | null;
  signal_id: number | null;
  signal_status: string | null;
  became_ready: boolean;
}

// -- Auth / multi-account (2026-09) ------------------------------------------

export interface User {
  id: number;
  username: string;
  display_name: string;
}

export interface MT5Account {
  id: number;
  label: string;
  mt5_login: string;
  mt5_server: string;
  symbol: string;
  //: Heartbeat-derived (concurrent-execution "Phase 2" update) -- true
  //: while `last_heartbeat_at` is recent, not a manually-toggled flag.
  is_live: boolean;
  last_heartbeat_at: string | null;
  is_active: boolean;
  has_password: boolean;
}

export interface MeResponse {
  user: User;
  accounts: MT5Account[];
}

export interface LoginRequest {
  username: string;
  password: string;
  remember_me?: boolean;
}

export interface MT5AccountCreate {
  label: string;
  mt5_login: string;
  mt5_server: string;
  mt5_password: string;
  symbol?: string;
}

export interface MT5AccountUpdate {
  label?: string;
  mt5_login?: string;
  mt5_server?: string;
  mt5_password?: string;
  symbol?: string;
}
