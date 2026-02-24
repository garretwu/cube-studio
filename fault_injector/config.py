from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json


@dataclass(slots=True)
class ServerConfig:
    name: str
    host: str
    user: str
    port: int
    interface: str
    original_mtu: int
    fault_mtu: int


@dataclass(slots=True)
class InjectorConfig:
    mode: str
    wal_path: str
    timeout: int
    servers: list[ServerConfig]


def load_config(path: str) -> InjectorConfig:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    injector = data["injector"]
    servers = [
        ServerConfig(
            name=item["name"],
            host=item["host"],
            user=item["user"],
            port=int(item.get("port", 22)),
            interface=item["interface"],
            original_mtu=int(item["original_mtu"]),
            fault_mtu=int(item["fault_mtu"]),
        )
        for item in data["servers"]
    ]
    return InjectorConfig(
        mode=injector.get("mode", "simulate"),
        wal_path=injector.get("wal_path", "fault_injector/rollback.wal"),
        timeout=int(injector.get("timeout", 20)),
        servers=servers,
    )
