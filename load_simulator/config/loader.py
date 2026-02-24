"""Load and validate YAML config files into LoadSimulatorConfig."""
from __future__ import annotations

from pathlib import Path
from typing import Union

import yaml
from pydantic import ValidationError

from load_simulator.config.defaults import DEFAULT_CONFIG_YAML
from load_simulator.config.schema import LoadSimulatorConfig


def load_config(path: Union[str, Path]) -> LoadSimulatorConfig:
    """Load a YAML config file and validate it into a LoadSimulatorConfig.

    Args:
        path: Path to the YAML file.

    Returns:
        A fully-validated LoadSimulatorConfig instance.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If YAML parsing or Pydantic validation fails.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {p}")

    raw = p.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Failed to parse YAML config: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Config file must contain a YAML mapping, got: {type(data).__name__}")

    try:
        return LoadSimulatorConfig(**data)
    except ValidationError as exc:
        raise ValueError(f"Config validation error:\n{exc}") from exc


def load_default_config() -> LoadSimulatorConfig:
    """Return a LoadSimulatorConfig populated from the built-in defaults."""
    data = yaml.safe_load(DEFAULT_CONFIG_YAML) or {}
    return LoadSimulatorConfig(**data)
