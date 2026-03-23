from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sre_agent.nat.wrapper import NATWrappedSREAgent


class _FakeRunner:
    async def adiagnose(self, payload: dict[str, str]) -> dict[str, str]:
        return {"root_cause": payload["alert_name"]}


class TestNATUnit:
    @pytest.mark.asyncio
    async def test_unit_wrapper_passthroughs_runner_when_called(self) -> None:
        wrapper = NATWrappedSREAgent(_FakeRunner(), "workflow.yml", "eval.jsonl")
        result = await wrapper.run_diagnosis({"alert_name": "gpu contention"})

        assert result["root_cause"] == "gpu contention"


class TestNATIntegration:
    def test_integration_loads_workflow_and_dataset_files_when_repo_artifacts_exist(self) -> None:
        nat_dir = Path(__file__).resolve().parent.parent / "nat"
        workflow = yaml.safe_load((nat_dir / "workflow.yml").read_text(encoding="utf-8"))
        dataset_line = (nat_dir / "eval_dataset.jsonl").read_text(encoding="utf-8").strip()

        assert workflow["workflow"]["name"] == "aidc-auto-sre"
        assert '"expected_root_cause"' in dataset_line


class TestNATE2E:
    @pytest.mark.asyncio
    async def test_e2e_fake_runner_still_returns_result_when_wrapped(self) -> None:
        nat_dir = Path(__file__).resolve().parent.parent / "nat"
        wrapper = NATWrappedSREAgent(_FakeRunner(), str(nat_dir / "workflow.yml"), str(nat_dir / "eval_dataset.jsonl"))

        result = await wrapper.run_diagnosis({"alert_name": "vllm_latency_high"})

        assert result == {"root_cause": "vllm_latency_high"}
