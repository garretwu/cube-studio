from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fault_injector.config.schema import TargetNodeConfig
from lib.channels.ssh import SSHChannel
from sre_agent.agent.nodes import _normalize_step_params_in_place
from sre_agent.models.diagnosis import DiagnosisResult
from sre_agent.runtime.node_mapping import normalize_node_identifier
from sre_agent.tools import build_default_registry


def _write_inventory(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "inventory:",
                "  workers:",
                "    - name: worker-03",
                "      ssh:",
                "        host: 10.11.4.12",
                "        user: demo",
                "    - name: worker-04",
                "      k8s_node_name: wj-lab-cpt-04",
                "      ssh:",
                "        host: 10.11.4.13",
                "        user: demo",
            ]
        ),
        encoding="utf-8",
    )


def _diagnosis_result() -> DiagnosisResult:
    return DiagnosisResult.model_validate(
        {
            "root_cause": "network jitter",
            "root_cause_layer": "network",
            "root_cause_entities": ["10.11.0.12"],
            "confidence": 0.8,
            "impact_summary": "latency high",
            "affected_services": ["inference-service"],
            "triage_priority": "P1",
            "diagnosis_certainty": "probable",
            "hypotheses": [],
        }
    )


class TestNodeMapping(unittest.TestCase):
    def test_tc_qdisc_summary_extracts_iface_parent_handle(self) -> None:
        from sre_agent.agent.nodes import _summarize_tc_qdisc

        summary, fields = _summarize_tc_qdisc(
            {
                "output": "qdisc netem 8016: dev net1 parent :3f limit 1000 delay 60ms",
                "iface": None,
            }
        )
        self.assertIn("iface=net1", summary)
        self.assertIsInstance(fields, dict)
        self.assertEqual(fields.get("iface"), "net1")
        self.assertEqual(fields.get("parent"), ":3f")

    def test_normalize_node_identifier_maps_internal_ip_to_inventory_node(self) -> None:
        normalized, reason = normalize_node_identifier(
            "10.11.0.12",
            inventory_names={"worker-03"},
            host_to_name={"10.11.4.12": "worker-03"},
        )
        self.assertEqual(normalized, "worker-03")
        self.assertIsNone(reason)

    def test_normalize_node_identifier_supports_env_override_mapping(self) -> None:
        with patch.dict(os.environ, {"SRE_NODE_IP_MAPPING_JSON": '{"172.16.1.8":"10.11.4.12"}'}, clear=False):
            normalized, reason = normalize_node_identifier(
                "172.16.1.8",
                inventory_names={"worker-03"},
                host_to_name={"10.11.4.12": "worker-03"},
            )
        self.assertEqual(normalized, "worker-03")
        self.assertIsNone(reason)

    def test_plan_normalization_rewrites_internal_ip_node_to_inventory_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            inventory_path = Path(tmpdir) / "inventory.yaml"
            _write_inventory(inventory_path)
            with patch.dict(os.environ, {"SRE_SSH_INVENTORY_PATH": str(inventory_path)}, clear=False):
                step = {
                    "tool": "network.clear_tc_qdisc",
                    "description": "clear tc qdisc parent :3f on node 10.11.0.12",
                    "params": {"node": "10.11.0.12", "iface": "net1", "parent": ":3f"},
                }
                error = _normalize_step_params_in_place(
                    step,
                    diagnosis=_diagnosis_result(),
                    registry=build_default_registry(),
                )

        self.assertIsNone(error)
        self.assertEqual(step["params"]["node"], "worker-03")

    def test_plan_normalization_returns_clear_error_when_mapped_host_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            inventory_path = Path(tmpdir) / "inventory.yaml"
            _write_inventory(inventory_path)
            with patch.dict(os.environ, {"SRE_SSH_INVENTORY_PATH": str(inventory_path)}, clear=False):
                step = {
                    "tool": "network.clear_tc_qdisc",
                    "description": "clear tc qdisc on node 10.11.0.99",
                    "params": {"node": "10.11.0.99", "iface": "net1"},
                }
                error = _normalize_step_params_in_place(
                    step,
                    diagnosis=_diagnosis_result(),
                    registry=build_default_registry(),
                )

        self.assertIsInstance(error, str)
        self.assertIn("missing_required_params: ['node']", str(error))
        self.assertIn("mapped_host_not_found:10.11.4.99", str(error))

    def test_iface_unknown_is_treated_as_missing_and_inferred_from_tool_runs(self) -> None:
        """iface='unknown' should be treated as missing and inferred from diagnosis tool_runs."""
        diagnosis = _diagnosis_result()
        step = {
            "tool": "network.clear_tc_qdisc",
            "description": "clear tc qdisc on worker-03",
            "params": {"node": "worker-03", "iface": "unknown"},
        }
        with patch.dict(os.environ, {"SRE_SSH_INVENTORY_PATH": ""}, clear=False):
            error = _normalize_step_params_in_place(
                step,
                diagnosis=diagnosis,
                registry=build_default_registry(),
            )
        # No tool_runs in diagnosis → no candidates → should report missing iface
        self.assertIsInstance(error, str)
        self.assertIn("missing_required_params", error)
        self.assertIn("iface", error)

    def test_iface_unknown_inferred_from_tool_runs_in_real_normalization_path(self) -> None:
        diagnosis = _diagnosis_result()
        step = {
            "tool": "network.clear_tc_qdisc",
            "description": "clear tc qdisc on worker-03",
            "params": {"node": "worker-03", "iface": "unknown", "parent": ":3f"},
        }
        tool_runs = [
            {
                "tool": "network.get_tc_qdisc",
                "params": {"node": "worker-03"},
                "key_fields": {"iface": "net1", "parent": ":3f", "netem_present": True},
            }
        ]
        with patch.dict(os.environ, {"SRE_SSH_INVENTORY_PATH": ""}, clear=False):
            error = _normalize_step_params_in_place(
                step,
                diagnosis=diagnosis,
                registry=build_default_registry(),
                tool_runs=tool_runs,
            )
        self.assertIsNone(error)
        self.assertEqual(step["params"]["iface"], "net1")

    def test_iface_unknown_uses_variables_when_no_step_or_tool_run_candidate(self) -> None:
        diagnosis = _diagnosis_result()
        step = {
            "tool": "network.clear_tc_qdisc",
            "description": "clear tc qdisc on worker-03",
            "params": {"node": "worker-03", "iface": "unknown"},
        }
        with patch.dict(os.environ, {"SRE_SSH_INVENTORY_PATH": ""}, clear=False):
            error = _normalize_step_params_in_place(
                step,
                diagnosis=diagnosis,
                registry=build_default_registry(),
                variables={"iface": "roce"},
            )
        self.assertIsNone(error)
        self.assertEqual(step["params"]["iface"], "roce")

    def test_iface_unknown_with_multiple_candidates_returns_ambiguous(self) -> None:
        diagnosis = _diagnosis_result()
        step = {
            "tool": "network.clear_tc_qdisc",
            "description": "clear tc qdisc on worker-03",
            "params": {"node": "worker-03", "iface": "unknown"},
        }
        tool_runs = [
            {"tool": "network.get_tc_qdisc", "params": {"node": "worker-03"}, "key_fields": {"iface": "eth0"}},
            {"tool": "network.get_tc_qdisc", "params": {"node": "worker-03"}, "key_fields": {"iface": "roce"}},
        ]
        with patch.dict(os.environ, {"SRE_SSH_INVENTORY_PATH": ""}, clear=False):
            error = _normalize_step_params_in_place(
                step,
                diagnosis=diagnosis,
                registry=build_default_registry(),
                tool_runs=tool_runs,
            )
        self.assertEqual(error, "iface_inference_ambiguous")

    def test_iface_candidates_prefer_node_and_parent_handle_match(self) -> None:
        from sre_agent.agent.nodes import _collect_tc_iface_candidates

        diagnosis = _diagnosis_result()
        step = {
            "tool": "network.clear_tc_qdisc",
            "description": "clear tc qdisc on worker-03 parent :3f",
            "params": {"node": "worker-03", "iface": "unknown", "parent": ":3f"},
        }
        tool_runs = [
            {"tool": "network.get_tc_qdisc", "params": {"node": "worker-03"}, "key_fields": {"iface": "net1", "parent": ":3f"}},
            {"tool": "network.get_tc_qdisc", "params": {"node": "worker-04"}, "key_fields": {"iface": "eth0", "parent": ":3f"}},
            {"tool": "network.get_tc_qdisc", "params": {"node": "worker-03"}, "key_fields": {"iface": "roce", "parent": "8016:10"}},
        ]

        candidates = _collect_tc_iface_candidates(step, diagnosis, tool_runs=tool_runs)
        self.assertEqual(candidates, ["net1"])

    def test_iface_placeholder_values_treated_as_missing(self) -> None:
        """Various placeholder values (n/a, None, empty) are treated as missing."""
        from sre_agent.agent.nodes import _is_placeholder_param

        for value in [None, "", "unknown", "n/a", "none", "-", "--", "null", "Unknown", "N/A", "  unknown  "]:
            self.assertTrue(
                _is_placeholder_param(value),
                f"Expected {value!r} to be treated as placeholder",
            )

    def test_iface_valid_value_not_treated_as_placeholder(self) -> None:
        """Valid iface names should NOT be treated as placeholders."""
        from sre_agent.agent.nodes import _is_placeholder_param

        for value in ["eth0", "roce", "ens192", "net1", "enp0s3"]:
            self.assertFalse(
                _is_placeholder_param(value),
                f"Expected {value!r} to NOT be treated as placeholder",
            )

    def test_ssh_channel_resolves_internal_ip_before_execution(self) -> None:
        inventory = {
            "worker-03": TargetNodeConfig.model_validate(
                {
                    "name": "worker-03",
                    "ssh": {"host": "10.11.4.12", "user": "demo"},
                }
            )
        }
        channel = SSHChannel(inventory=inventory, dry_run=True)
        self.assertEqual(channel._resolve_node_name("10.11.0.12"), "worker-03")

    def test_normalize_node_identifier_supports_k8s_node_name(self) -> None:
        """k8s_node_name should be mapped to inventory worker name."""
        normalized, reason = normalize_node_identifier(
            "wj-lab-cpt-04",
            inventory_names={"worker-04"},
            host_to_name={"10.11.4.13": "worker-04", "wj-lab-cpt-04": "worker-04"},
        )
        self.assertEqual(normalized, "worker-04")
        self.assertIsNone(reason)

    def test_ssh_channel_resolves_k8s_node_name_to_inventory_worker(self) -> None:
        """SSH channel should resolve k8s_node_name to worker name."""
        inventory = {
            "worker-04": TargetNodeConfig.model_validate(
                {
                    "name": "worker-04",
                    "ssh": {"host": "10.11.4.13", "user": "demo"},
                }
            )
        }
        # Create channel with k8s_node_name in the IP mapping
        channel = SSHChannel(inventory=inventory, dry_run=True)
        # Simulate having k8s_node_name mapped (this is done in node_mapping.py)
        channel._ip_to_node["wj-lab-cpt-04"] = "worker-04"
        self.assertEqual(channel._resolve_node_name("wj-lab-cpt-04"), "worker-04")

    def test_load_inventory_node_mapping_extracts_k8s_node_name(self) -> None:
        """load_inventory_node_mapping should extract k8s_node_name from inventory yaml."""
        from sre_agent.runtime.node_mapping import load_inventory_node_mapping

        with tempfile.TemporaryDirectory() as tmpdir:
            inventory_path = Path(tmpdir) / "inventory.yaml"
            _write_inventory(inventory_path)
            with patch.dict(os.environ, {"SRE_SSH_INVENTORY_PATH": str(inventory_path)}, clear=False):
                names, host_to_name = load_inventory_node_mapping()

        self.assertIn("worker-03", names)
        self.assertIn("worker-04", names)
        # SSH host IP mapping
        self.assertEqual(host_to_name.get("10.11.4.12"), "worker-03")
        self.assertEqual(host_to_name.get("10.11.4.13"), "worker-04")
        # k8s_node_name mapping
        self.assertEqual(host_to_name.get("wj-lab-cpt-04"), "worker-04")


if __name__ == "__main__":
    unittest.main()
