"""Minimal config loader for CLI flows."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class GlobalConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    aidc_id: str = "aidc-demo"


class SwitchPortDiscoveryConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    name: str
    connected_to: str | None = None
    status: str = "up"
    speed_gbps: int | None = None


class SwitchDiscoveryConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    name: str
    type: str | None = None
    status: str = "online"
    ports: list[SwitchPortDiscoveryConfig] = Field(default_factory=list)


class OntologyDiscoveryConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    switches: list[SwitchDiscoveryConfig] = Field(default_factory=list)


class OntologyConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    db_path: str = "./data/ontology.db"
    discovery: OntologyDiscoveryConfig = Field(default_factory=OntologyDiscoveryConfig)


class SREAgentConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    global_: GlobalConfig = Field(default_factory=GlobalConfig, alias="global")
    ontology: OntologyConfig = Field(default_factory=OntologyConfig)


def load_config(path: str | Path) -> SREAgentConfig:
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return SREAgentConfig.model_validate(raw)
