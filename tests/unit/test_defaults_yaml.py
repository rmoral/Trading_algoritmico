"""Guard against drift between `config/defaults.yaml` and the schema."""

from __future__ import annotations

from pathlib import Path

import yaml

from tradingbot_api.config_schema import ConfigPolicyPayload

DEFAULTS_PATH = Path(__file__).resolve().parents[2] / "config" / "defaults.yaml"


def test_defaults_yaml_validates_against_schema() -> None:
    raw = yaml.safe_load(DEFAULTS_PATH.read_text())
    ConfigPolicyPayload.model_validate(raw)
