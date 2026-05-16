"""HTTP routes for the web API.

Grouped in a single module while the surface is small. When endpoints
multiply we will split per domain (auth, dashboard, config, ...).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker

from tradingbot.persistence.models import User
from tradingbot.persistence.repositories import PnLRepository, PositionsRepository
from tradingbot.settings import Settings
from tradingbot_api.auth import (
    SESSION_COOKIE_NAME,
    create_session,
    delete_session,
    get_current_user,
    get_session_factory_dep,
    get_settings_dep,
    verify_password,
)
from tradingbot_api.schemas import (
    BotStatusResponse,
    HealthResponse,
    LoginRequest,
    OpenPositionResponse,
    PnLResponse,
    UserResponse,
)

health_router = APIRouter(tags=["health"])
auth_router = APIRouter(prefix="/api/auth", tags=["auth"])
me_router = APIRouter(prefix="/api/me", tags=["me"])
status_router = APIRouter(prefix="/api/status", tags=["status"])


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
    request: Request,
    session_factory: Annotated[
        async_sessionmaker[_AsyncSession], Depends(get_session_factory_dep)
    ],
    _user: Annotated[User, Depends(get_current_user)],
) -> BotStatusResponse:
    positions_repo = PositionsRepository(session_factory)
    pnl_repo = PnLRepository(session_factory)

    open_pos = await positions_repo.get_open_position()
    pnl_row = await pnl_repo.get_pnl_for_date(datetime.now(UTC).date())

    kill_switch = getattr(request.app.state, "kill_switch_view", None)
    kill_tripped = bool(kill_switch.tripped) if kill_switch is not None else False
    kill_reason = kill_switch.reason if kill_switch is not None else None

    return BotStatusResponse(
        kill_switch_tripped=kill_tripped,
        kill_switch_reason=kill_reason,
        open_position=(
            OpenPositionResponse.model_validate(open_pos) if open_pos else None
        ),
        today_pnl=PnLResponse.model_validate(pnl_row) if pnl_row else None,
    )
