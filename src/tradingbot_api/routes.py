"""HTTP routes for the web API.

Grouped in a single module while the surface is small. When endpoints
multiply we will split per domain (auth, dashboard, config, ...).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker

from tradingbot.persistence.models import User
from tradingbot.persistence.repositories import PnLRepository, PositionsRepository
from tradingbot.settings import Settings
from tradingbot.state import KILL_REQUEST_KEY, BotStateReader
from tradingbot_api.asset_service import (
    AssetChangeBlockedError,
    get_current_asset,
    set_active_asset,
)
from tradingbot_api.auth import (
    SESSION_COOKIE_NAME,
    create_session,
    delete_session,
    get_current_user,
    get_redis_dep,
    get_session_factory_dep,
    get_settings_dep,
    verify_password,
)
from tradingbot_api.config_schema import ConfigPolicyPayload
from tradingbot_api.config_service import get_current_policy, update_policy
from tradingbot_api.schemas import (
    ActiveAssetResponse,
    BotStatusResponse,
    ConfigPolicyResponse,
    HealthResponse,
    KillRequest,
    LoginRequest,
    OpenPositionResponse,
    PnLResponse,
    SetActiveAssetRequest,
    UserResponse,
)

health_router = APIRouter(tags=["health"])
auth_router = APIRouter(prefix="/api/auth", tags=["auth"])
me_router = APIRouter(prefix="/api/me", tags=["me"])
status_router = APIRouter(prefix="/api/status", tags=["status"])
config_router = APIRouter(prefix="/api/config", tags=["config"])
asset_router = APIRouter(prefix="/api/active-asset", tags=["asset"])
kill_router = APIRouter(prefix="/api/kill", tags=["kill"])


# =========================================================
# Health
# =========================================================


@health_router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    return HealthResponse()


# =========================================================
# Auth
# =========================================================


@auth_router.post("/login", response_model=UserResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    session_factory: Annotated[
        async_sessionmaker[_AsyncSession], Depends(get_session_factory_dep)
    ],
) -> User:
    async with session_factory() as db:
        user = (
            await db.execute(select(User).where(User.username == body.username))
        ).scalar_one_or_none()

        if user is None or not user.is_active:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
        if not verify_password(body.password, user.password_hash):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")

        user.last_login_at = datetime.now(UTC)
        client_host = request.client.host if request.client else None
        session = await create_session(
            db, user=user, settings=settings, ip_address=client_host
        )
        await db.commit()

        max_age = settings.web_session_lifetime_days * 24 * 3600
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=str(session.id),
            max_age=max_age,
            httponly=True,
            secure=settings.web_cookie_secure,
            samesite="lax",
            path="/",
        )
        return user


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    session_factory: Annotated[
        async_sessionmaker[_AsyncSession], Depends(get_session_factory_dep)
    ],
    _user: Annotated[User, Depends(get_current_user)],
) -> Response:
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie:
        from uuid import UUID

        try:
            session_id = UUID(cookie)
        except ValueError:
            pass
        else:
            async with session_factory() as db:
                await delete_session(db, session_id)
                await db.commit()
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


# =========================================================
# Me
# =========================================================


@me_router.get("", response_model=UserResponse)
async def me(user: Annotated[User, Depends(get_current_user)]) -> User:
    return user


# =========================================================
# Bot status (dashboard poll)
# =========================================================


@status_router.get("", response_model=BotStatusResponse)
async def bot_status(
    session_factory: Annotated[
        async_sessionmaker[_AsyncSession], Depends(get_session_factory_dep)
    ],
    redis: Annotated[Redis, Depends(get_redis_dep)],  # type: ignore[type-arg]
    _user: Annotated[User, Depends(get_current_user)],
) -> BotStatusResponse:
    positions_repo = PositionsRepository(session_factory)
    pnl_repo = PnLRepository(session_factory)
    state_reader = BotStateReader(redis)

    open_pos = await positions_repo.get_open_position()
    pnl_row = await pnl_repo.get_pnl_for_date(datetime.now(UTC).date())

    connection_raw = await state_reader.get_connection()
    if connection_raw is None:
        connection_state = "unknown"
    elif connection_raw:
        connection_state = "connected"
    else:
        connection_state = "disconnected"

    kill = await state_reader.get_kill_switch()
    kill_tripped = bool(kill.tripped) if kill is not None else False
    kill_reason = kill.reason if kill is not None else None

    return BotStatusResponse(
        connection_state=connection_state,
        kill_switch_tripped=kill_tripped,
        kill_switch_reason=kill_reason,
        open_position=(
            OpenPositionResponse.model_validate(open_pos) if open_pos else None
        ),
        today_pnl=PnLResponse.model_validate(pnl_row) if pnl_row else None,
    )


# =========================================================
# Runtime configuration (config_policies)
# =========================================================


@config_router.get("", response_model=ConfigPolicyResponse)
async def read_config(
    session_factory: Annotated[
        async_sessionmaker[_AsyncSession], Depends(get_session_factory_dep)
    ],
    _user: Annotated[User, Depends(get_current_user)],
) -> ConfigPolicyResponse:
    async with session_factory() as db:
        current = await get_current_policy(db)
        if current is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "no active config policy"
            )
        return ConfigPolicyResponse.model_validate(current)


@config_router.put("", response_model=ConfigPolicyResponse)
async def write_config(
    body: ConfigPolicyPayload,
    session_factory: Annotated[
        async_sessionmaker[_AsyncSession], Depends(get_session_factory_dep)
    ],
    user: Annotated[User, Depends(get_current_user)],
) -> ConfigPolicyResponse:
    async with session_factory() as db:
        new_policy = await update_policy(db, payload=body, actor=user.username)
        await db.commit()
        await db.refresh(new_policy)
        return ConfigPolicyResponse.model_validate(new_policy)


# =========================================================
# Daily active asset
# =========================================================


@asset_router.get("", response_model=ActiveAssetResponse)
async def read_active_asset(
    session_factory: Annotated[
        async_sessionmaker[_AsyncSession], Depends(get_session_factory_dep)
    ],
    _user: Annotated[User, Depends(get_current_user)],
) -> ActiveAssetResponse:
    async with session_factory() as db:
        current = await get_current_asset(db)
        if current is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "no active asset selected"
            )
        return ActiveAssetResponse.model_validate(current)


@asset_router.put("", response_model=ActiveAssetResponse)
async def write_active_asset(
    body: SetActiveAssetRequest,
    session_factory: Annotated[
        async_sessionmaker[_AsyncSession], Depends(get_session_factory_dep)
    ],
    user: Annotated[User, Depends(get_current_user)],
) -> ActiveAssetResponse:
    positions_repo = PositionsRepository(session_factory)
    async with session_factory() as db:
        try:
            new = await set_active_asset(
                db,
                symbol=body.symbol,
                actor=user.username,
                positions_repo=positions_repo,
            )
        except AssetChangeBlockedError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        await db.commit()
        await db.refresh(new)
        return ActiveAssetResponse.model_validate(new)


# =========================================================
# Kill switch (remote trip from the web app)
# =========================================================


@kill_router.post("", status_code=status.HTTP_202_ACCEPTED)
async def request_kill(
    body: KillRequest,
    redis: Annotated[Redis, Depends(get_redis_dep)],  # type: ignore[type-arg]
    user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Request the bot to trip its kill switch.

    202 Accepted: the request is queued in Redis; the bot polls and
    applies on its next interval (typically within 2 s). Successive
    requests are idempotent: a re-poll sees the request even if a
    previous one was already consumed.
    """
    reason = f"web:{user.username}"
    if body.reason:
        reason = f"{reason}:{body.reason}"
    await redis.set(KILL_REQUEST_KEY, reason)
    return Response(status_code=status.HTTP_202_ACCEPTED)
