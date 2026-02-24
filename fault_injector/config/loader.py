"""
配置加载器

从 YAML 文件加载配置并进行校验。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

from fault_injector.config.schema import (
    FaultInjectorConfig,
    GlobalConfig,
    SafetyConfig,
    TargetNodeConfig,
    SSHConfig,
    ScenarioConfig,
)
from fault_injector.config.defaults import get_default_config


def load_config(path: str) -> FaultInjectorConfig:
    """
    从 YAML 文件加载配置。
    
    Args:
        path: YAML 配置文件路径
        
    Returns:
        FaultInjectorConfig: 校验后的配置对象
        
    Raises:
        FileNotFoundError: 配置文件不存在
        ValueError: 配置校验失败
    """
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    
    # 读取 YAML 文件
    with open(config_path, "r", encoding="utf-8") as f:
        raw_config = yaml.safe_load(f)
    
    if raw_config is None:
        raw_config = {}
    
    # 解析并校验配置
    try:
        config = _parse_config(raw_config)
    except Exception as e:
        raise ValueError(f"配置校验失败: {e}") from e
    
    return config


def _parse_config(raw: dict[str, Any]) -> FaultInjectorConfig:
    """解析原始配置字典为 FaultInjectorConfig"""
    
    # 解析全局配置
    global_raw = raw.get("global", {})
    safety_raw = global_raw.get("safety", {})
    
    safety = SafetyConfig(
        require_confirmation=safety_raw.get("require_confirmation", True),
        auto_recover_timeout=safety_raw.get("auto_recover_timeout", 600),
        dry_run=safety_raw.get("dry_run", False),
        max_concurrent_faults=safety_raw.get("max_concurrent_faults", 3),
        excluded_nodes=safety_raw.get("excluded_nodes", []),
    )
    
    global_config = GlobalConfig(
        session_dir=global_raw.get("session_dir", "./fault-reports/sessions/"),
        log_level=global_raw.get("log_level", "INFO"),
        safety=safety,
    )
    
    # 解析节点清单
    inventory: dict[str, list[TargetNodeConfig]] = {}
    inventory_raw = raw.get("inventory", {})
    
    for group_name, nodes in inventory_raw.items():
        if not isinstance(nodes, list):
            continue
        inventory[group_name] = []
        for node_raw in nodes:
            if not isinstance(node_raw, dict):
                continue
            ssh_raw = node_raw.get("ssh", {})
            ssh = SSHConfig(
                host=ssh_raw.get("host", ""),
                port=ssh_raw.get("port", 22),
                user=ssh_raw.get("user", "root"),
                key_file=ssh_raw.get("key_file"),
                password=ssh_raw.get("password"),
                timeout=ssh_raw.get("timeout", 30),
            )
            node = TargetNodeConfig(
                name=node_raw.get("name", ""),
                ssh=ssh,
                interface=node_raw.get("interface", "eth0"),
                roles=node_raw.get("roles", []),
            )
            inventory[group_name].append(node)
    
    # 解析场景配置
    scenarios: dict[str, ScenarioConfig] = {}
    scenarios_raw = raw.get("scenarios", {})
    
    for scenario_name, scenario_raw in scenarios_raw.items():
        if not isinstance(scenario_raw, dict):
            continue
        scenario = ScenarioConfig(
            name=scenario_raw.get("name", scenario_name),
            enabled=scenario_raw.get("enabled", True),
            target_nodes=scenario_raw.get("target_nodes", []),
            params=scenario_raw.get("params", {}),
        )
        scenarios[scenario_name] = scenario
    
    # 使用 model_validate 避免 global 保留字问题
    return FaultInjectorConfig(
        global_=global_config,
        inventory=inventory,
        scenarios=scenarios,
    )


def _compute_hash(path: Path) -> str:
    """计算文件 SHA256 哈希"""
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256.update(chunk)
    return sha256.hexdigest()[:16]