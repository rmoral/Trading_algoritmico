"""Request/response models for the web API.

Kept in a single module while the surface is small; split per route
when it grows.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=255)
    totp_code: str | None = Field(default=None, pattern=r"^[0-9]{6}$")


class TOTPEnrollResponse(BaseModel):
    secret: str
    provisioning_uri: str


class TOTPVerifyRequest(BaseModel):
    secret: str = Field(min_length=16, max_length=64)
    code: str = Field(pattern=r"^[0-9]{6}$")


class TOTPDisenrollRequest(BaseModel):
    code: str = Field(pattern=r"^[0-9]{6}$")


class UserResponse(BaseModel):
    id: UUID
    username: str
    is_active: bool
    last_login_at: datetime | None = None
    totp_enrolled: bool = False


class OpenPositionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    side: str
    qty: Decimal
    avg_entry_price: Decimal
    state: str


class PnLResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    date: date
    gross_pnl: Decimal
    commissions: Decimal
    net_pnl: Decimal
    n_trades: int
    n_wins: int
    n_losses: int


ConnectionState = Literal["connected", "disconnected", "unknown"]


class BotStatusResponse(BaseModel):
    """High-level snapshot for the dashboard poll endpoint."""

    connection_state: ConnectionState
    kill_switch_tripped: bool
    kill_switch_reason: str | None
    open_position: OpenPositionResponse | None
    today_pnl: PnLResponse | None


class KillRequest(BaseModel):
    """Body for POST /api/kill."""

    reason: str | None = Field(default=None, max_length=255)


class HealthResponse(BaseModel):
    status: str = "ok"


class ConfigPolicyResponse(BaseModel):
    """Wire format for a single policy version."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version: int
    effective_from: datetime
    effective_to: datetime | None
    payload: dict[str, Any]
    created_by: str
    created_at: datetime


class SetActiveAssetRequest(BaseModel):
    """Body for PUT /api/active-asset."""

    symbol: str = Field(
        min_length=1,
        max_length=8,
        pattern=r"^[A-Z][A-Z0-9.]{0,7}$",
        description="US ticker symbol, e.g. AAPL, MSFT, BRK.B.",
    )


class ActiveAssetResponse(BaseModel):
    """Wire format for the current active asset selection."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    symbol: str
    effective_from: datetime
    effective_to: datetime | None
    set_by: str
