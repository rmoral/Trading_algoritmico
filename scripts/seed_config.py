"""Seed `config_policies` with bootstrap defaults from
`config/defaults.yaml`. Idempotent: a no-op when policies already exist.

Run with: `uv run python scripts/seed_config.py`.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml
from sqlalchemy import func, select

from tradingbot.logging_setup import configure_logging, get_logger
from tradingbot.persistence.database import create_engine, create_session_factory
from tradingbot.persistence.models import ConfigPolicy
from tradingbot.settings import get_settings
from tradingbot_api.config_schema import ConfigPolicyPayload

DEFAULTS_PATH = Path(__file__).resolve().parent.parent / "config" / "defaults.yaml"


async def seed() -> int:
    settings = get_settings()
    configure_logging(settings)
    log = get_logger("seed_config")

    if not DEFAULTS_PATH.exists():
        log.error("defaults_file_missing", path=str(DEFAULTS_PATH))
        return 1

    raw = yaml.safe_load(DEFAULTS_PATH.read_text())
    if not isinstance(raw, dict):
        log.error("defaults_file_invalid", path=str(DEFAULTS_PATH))
        return 1

    # Round-trip through the API's validator so seed_config can never
    # publish a payload the web app would refuse.
    payload = ConfigPolicyPayload.model_validate(raw)
    serialized = payload.model_dump(mode="json")

    engine = create_engine(settings)
    factory = create_session_factory(engine)

    try:
        async with factory() as session:
            count = await session.scalar(
                select(func.count()).select_from(ConfigPolicy)
            )
            if count and count > 0:
                log.info("config_policies_already_seeded", existing_rows=count)
                return 0

            now = datetime.now(UTC)
            policy = ConfigPolicy(
                version=1,
                effective_from=now,
                effective_to=None,
                payload=serialized,
                created_by="seed",
                created_at=now,
            )
            session.add(policy)
            await session.commit()
            log.info("config_policies_seeded", version=1)
            return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(asyncio.run(seed()))
