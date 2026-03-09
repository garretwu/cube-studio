"""Tests for load_simulator.config.loader."""
from __future__ import annotations

import os
import tempfile
import unittest

from load_simulator.config.loader import load_config, load_default_config
from load_simulator.config.schema import LoadSimulatorConfig


class LoadDefaultConfigTests(unittest.TestCase):
    def test_returns_valid_config(self):
        cfg = load_default_config()
        self.assertIsInstance(cfg, LoadSimulatorConfig)
        self.assertIn("inference", cfg.agents)
        self.assertEqual(cfg.mode, "single")

    def test_default_config_inference_section(self):
        cfg = load_default_config()
        self.assertTrue(cfg.inference.enabled)
        self.assertGreater(cfg.inference.concurrency, 0)
        self.assertGreater(cfg.inference.duration_seconds, 0)


class LoadConfigFromFileTests(unittest.TestCase):
    def test_load_minimal_yaml(self):
        content = """\
agents:
  - inference
mode: single
inference:
  endpoint: "http://test:8000/v1/chat/completions"
  model: "test-model"
  concurrency: 2
  duration_seconds: 10
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(content)
            f.flush()
            path = f.name

        try:
            cfg = load_config(path)
            self.assertIsInstance(cfg, LoadSimulatorConfig)
            self.assertEqual(cfg.inference.model, "test-model")
            self.assertEqual(cfg.inference.concurrency, 2)
        finally:
            os.unlink(path)

    def test_load_with_all_agents(self):
        content = """\
agents:
  - inference
  - pipeline
  - finetune
  - notebook
mode: mixed
inference:
  endpoint: "http://localhost:8000/v1/chat/completions"
  concurrency: 4
  duration_seconds: 30
pipeline:
  cube_studio_url: "http://localhost"
  concurrency: 2
  duration_seconds: 30
finetune:
  llama_factory_url: "http://localhost:7860"
  duration_seconds: 30
notebook:
  jupyter_url: "http://localhost:8888"
  duration_seconds: 30
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(content)
            f.flush()
            path = f.name

        try:
            cfg = load_config(path)
            self.assertEqual(len(cfg.agents), 4)
            self.assertEqual(cfg.mode, "mixed")
        finally:
            os.unlink(path)

    def test_extra_fields_ignored(self):
        content = """\
agents:
  - inference
mode: single
unknown_field: true
inference:
  endpoint: "http://localhost:8000/v1/chat/completions"
  extra_key: 42
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(content)
            f.flush()
            path = f.name

        try:
            cfg = load_config(path)
            self.assertIsInstance(cfg, LoadSimulatorConfig)
        finally:
            os.unlink(path)

    def test_env_var_expansion(self):
        os.environ["LS_TEST_JWT"] = "jwt-secret-value"
        content = """\
agents:
  - inference
mode: single
global_config:
  auth_method: jwt
  jwt_password: "${LS_TEST_JWT}"
inference:
  endpoint: "http://localhost:8000/v1/chat/completions"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(content)
            f.flush()
            path = f.name

        try:
            cfg = load_config(path)
            self.assertEqual(cfg.global_config.auth_method, "jwt")
            self.assertEqual(cfg.global_config.jwt_password, "jwt-secret-value")
        finally:
            os.unlink(path)
            os.environ.pop("LS_TEST_JWT", None)


if __name__ == "__main__":
    unittest.main()
