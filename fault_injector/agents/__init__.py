"""
Agents module - Fault injection agents for different layers.

This module provides specialized agents for injecting faults at different
infrastructure layers:
- HardwareFaultAgent: CPU, GPU, Memory, Network hardware
- OSFaultAgent: OS-level faults via SSH
- PlatformFaultAgent: K8s, MySQL, Redis, Celery
- ServiceFaultAgent: Inference services, Pipelines, Notebooks
- MonitorAgent: Metric collection
- DiagnosisAgent: LLM-powered fault diagnosis
"""
from fault_injector.agents.base import BaseAgent, AgentResult

__all__ = [
    "BaseAgent",
    "AgentResult",
]