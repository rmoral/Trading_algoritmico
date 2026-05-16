"""Request/response models for the web API.

Kept in a single module while the surface is small; split per route
when it grows.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=255)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    is_active: bool
    last_login_at: datetime | None = None


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


class BotStatusResponse(BaseModel):
    """High-level snapshot for the dashboard poll endpoint.

    Most fields read from Postgres; bot connection state and the kill
    switch will move to Redis once the bot publishes them.
    """

    kill_switch_tripped: bool
    kill_switch_reason: str | None
    open_position: OpenPositionResponse | None
    today_pnl: PnLResponse | None


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
