"""
SafetyGuard — 安全守卫

硬编码禁止操作拦截、确认门控、并发故障数限制。
"""
from __future__ import annotations

import logging
import re
import shlex
from typing import Any

from fault_injector.config.schema import SafetyConfig

logger = logging.getLogger(__name__)


class SafetyViolationError(Exception):
    """安全违规异常"""
    pass


class SafetyGuard:
    """
    安全守卫 — 多层防护。
    
    功能：
    1. 禁止操作检查 — 硬编码拦截危险命令
    2. 命令注入检查 — 拦截 shell 元字符
    3. 并发限制 — 控制同时注入的故障数
    4. 确认门控 — 危险操作需要用户确认
    """
    
    # 硬编码禁止操作列表
    FORBIDDEN_PATTERNS = [
        # 全盘删除
        r"rm\s+-rf\s+/",
        r"rm\s+-rf\s+/*",
        r"dd\s+if=/dev/zero",
        r"dd\s+if=/dev/urandom",
        # SSH 服务配置
        r"systemctl\s+(stop|disable)\s+ssh",
        r"systemctl\s+(stop|disable)\s+sshd",
        # BMC 恢复出厂设置
        r"FactoryReset",
        r"RestoreFactory",
        # K8s namespace 删除
        r"kubectl\s+delete\s+namespace",
        r"kubectl\s+delete\s+ns\s+--all",
        # etcd 数据删除
        r"etcdctl\s+del\s+",
        r"rm\s+.*etcd",
        # 交换机恢复出厂
        r"restore\s+factory",
        r"reset\s+saved-configuration",
        r"\breload\b",
        r"\breboot\b",
    ]
    
    # 危险 shell 元字符
    SHELL_META_CHARS = [";", "|", "&", "`", "$(", "${", "&&", "||"]
    
    # 路径白名单（用于日志读取等）
    ALLOWED_LOG_PATHS = {
        "/var/log/syslog",
        "/var/log/messages",
        "/var/log/kern.log",
        "/var/log/dmesg",
        "/var/log/auth.log",
        "/var/log/gpu-manager.log",
    }
    
    def __init__(self, config: SafetyConfig):
        """
        初始化安全守卫。
        
        Args:
            config: 安全配置
        """
        self.config = config
        self._active_faults = 0
    
    def check_command(self, command: str, channel: str = "ssh") -> None:
        """
        检查命令是否安全。
        
        Args:
            command: 要执行的命令
            channel: 执行通道
            
        Raises:
            SafetyViolationError: 命令被禁止
        """
        # 1. 检查禁止模式
        for pattern in self.FORBIDDEN_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                raise SafetyViolationError(
                    f"命令被禁止 (匹配模式: {pattern}): {command}"
                )
        
        # 2. 检查危险 shell 元字符（仅 SSH 通道）
        if channel == "ssh":
            for char in self.SHELL_META_CHARS:
                if char in command:
                    # 允许一些合理的用例
                    if char in ["&&", "||"] and "|" not in command:
                        continue
                    raise SafetyViolationError(
                        f"命令包含危险的 shell 元字符 '{char}': {command}"
                    )
    
    def check_path(self, path: str, allowed_paths: set[str] | None = None) -> None:
        """
        检查路径是否在白名单中。
        
        Args:
            path: 要检查的路径
            allowed_paths: 允许的路径集合（默认使用日志路径白名单）
            
        Raises:
            SafetyViolationError: 路径不在白名单中
        """
        allowed = allowed_paths or self.ALLOWED_LOG_PATHS
        if path not in allowed:
            raise SafetyViolationError(
                f"路径不在白名单中: {path}。允许的路径: {allowed}"
            )
    
    def check_node(self, node: str) -> None:
        """
        检查节点是否在排除列表中。
        
        Args:
            node: 节点名称
            
        Raises:
            SafetyViolationError: 节点被排除
        """
        if node in self.config.excluded_nodes:
            raise SafetyViolationError(
                f"节点 '{node}' 在排除列表中，不允许注入故障"
            )
    
    def acquire_fault_slot(self) -> bool:
        """
        获取故障槽位（并发控制）。
        
        Returns:
            bool: 是否成功获取槽位
        """
        if self._active_faults >= self.config.max_concurrent_faults:
            logger.warning(
                f"已达到最大并发故障数 {self.config.max_concurrent_faults}"
            )
            return False
        self._active_faults += 1
        return True
    
    def release_fault_slot(self) -> None:
        """释放故障槽位"""
        if self._active_faults > 0:
            self._active_faults -= 1
    
    def should_confirm(self, action: str) -> bool:
        """
        判断是否需要用户确认。
        
        Args:
            action: 操作描述
            
        Returns:
            bool: 是否需要确认
        """
        if not self.config.require_confirmation:
            return False
        
        # 危险操作关键词
        dangerous_keywords = [
            "delete", "remove", "kill", "shutdown", "restart",
            "reboot", "poweroff", "drain", "reset",
        ]
        
        action_lower = action.lower()
        return any(kw in action_lower for kw in dangerous_keywords)
    
    def is_dry_run(self) -> bool:
        """是否为 dry-run 模式"""
        return self.config.dry_run
    
    def quote_arg(self, arg: str) -> str:
        """
        安全地引用参数（防命令注入）。
        
        Args:
            arg: 原始参数
            
        Returns:
            str: 引用后的参数
        """
        return shlex.quote(arg)
    
    def validate_tc_command(self, interface: str, delay_ms: int, 
                            jitter_ms: int = 0) -> str:
        """
        构建并验证 tc netem 命令。
        
        Args:
            interface: 网络接口
            delay_ms: 延迟毫秒数
            jitter_ms: 抖动毫秒数
            
        Returns:
            str: 验证后的命令
            
        Raises:
            SafetyViolationError: 参数无效
        """
        # 参数范围检查
        if delay_ms < 0 or delay_ms > 60000:
            raise SafetyViolationError(
                f"延迟值 {delay_ms}ms 超出有效范围 (0-60000)"
            )
        if jitter_ms < 0 or jitter_ms > 60000:
            raise SafetyViolationError(
                f"抖动值 {jitter_ms}ms 超出有效范围 (0-60000)"
            )
        
        # 接口名安全检查
        if not re.match(r"^[a-zA-Z0-9_-]+$", interface):
            raise SafetyViolationError(
                f"无效的接口名: {interface}"
            )
        
        # 构建命令（使用 quote 防注入）
        safe_interface = self.quote_arg(interface)
        cmd = f"tc qdisc add dev {safe_interface} root netem delay {delay_ms}ms"
        if jitter_ms > 0:
            cmd += f" {jitter_ms}ms"
        
        return cmd
    
    def get_status(self) -> dict[str, Any]:
        """获取安全守卫状态"""
        return {
            "dry_run": self.config.dry_run,
            "require_confirmation": self.config.require_confirmation,
            "max_concurrent_faults": self.config.max_concurrent_faults,
            "active_faults": self._active_faults,
            "excluded_nodes": self.config.excluded_nodes,
            "auto_recover_timeout": self.config.auto_recover_timeout,
        }
