"""
默认配置

提供 Fault Injector 的内置默认值。
"""
from fault_injector.config.schema import (
    FaultInjectorConfig,
    GlobalConfig,
    SafetyConfig,
)


def get_default_config() -> FaultInjectorConfig:
    """获取默认配置"""
    global_config = GlobalConfig(
        session_dir="./fault-reports/sessions/",
        log_level="INFO",
        safety=SafetyConfig(
            require_confirmation=True,
            auto_recover_timeout=600,
            dry_run=False,
            max_concurrent_faults=3,
            excluded_nodes=[],
        ),
    )
    
    return FaultInjectorConfig(
        global_=global_config,
        inventory={},
        scenarios={},
    )


# 默认 YAML 配置模板
DEFAULT_CONFIG_YAML = """
# fault-injector-config.yaml
# AIDC Auto-SRE Fault Injector 默认配置

global:
  session_dir: "./fault-reports/sessions/"
  log_level: "INFO"
  safety:
    require_confirmation: true
    auto_recover_timeout: 600
    dry_run: false
    max_concurrent_faults: 3
    excluded_nodes: []

monitor:
  enabled: true
  prometheus_url: "http://10.11.4.3:31260"
  baseline_duration: 60
  post_recovery_duration: 60

inventory:
  nodes:
    - name: "test-vm-1"
      ssh:
        host: "192.168.1.100"
        port: 22
        user: "root"
        key_file: "~/.ssh/id_rsa"
      redfish:
        bmc_host: "192.168.1.110"
        username: "admin"
        password: "<SECRET>"
        verify_tls: true
        timeout: 30
      interface: "eth0"
      roles: []

scenarios:
  network_jitter:
    name: "network_jitter"
    enabled: true
    target_nodes: ["test-vm-1"]
    params:
      delay_ms: 50
      jitter_ms: 100
      distribution: "pareto"
      loss_pct: 0
      duration: 60
      interface: "eth0"
"""
