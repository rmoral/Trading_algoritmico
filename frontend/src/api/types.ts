// Wire types mirror the pydantic schemas in
// src/tradingbot_api/schemas.py. Kept hand-written until we wire up
// openapi-typescript codegen.

export type ConnectionState = "connected" | "disconnected" | "unknown";

export interface UserResponse {
  id: string;
  username: string;
  is_active: boolean;
  last_login_at: string | null;
}

export interface OpenPositionResponse {
  symbol: string;
  side: string;
  qty: string;
  avg_entry_price: string;
  state: string;
}

export interface PnLResponse {
  date: string;
  gross_pnl: string;
  commissions: string;
  net_pnl: string;
  n_trades: number;
  n_wins: number;
  n_losses: number;
}

export interface BotStatusResponse {
  connection_state: ConnectionState;
  kill_switch_tripped: boolean;
  kill_switch_reason: string | null;
  open_position: OpenPositionResponse | null;
  today_pnl: PnLResponse | null;
}

export interface ConfigPolicyResponse {
  id: string;
  version: number;
  effective_from: string;
  effective_to: string | null;
  payload: Record<string, unknown>;
  created_by: string;
  created_at: string;
}

export interface ActiveAssetResponse {
  id: string;
  symbol: string;
  effective_from: string;
  effective_to: string | null;
  set_by: string;
}

export interface TOTPEnrollResponse {
  secret: string;
  provisioning_uri: string;
}
