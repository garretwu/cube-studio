from __future__ import annotations

import unittest
from pathlib import Path


class SchemaContractTests(unittest.TestCase):
    def test_schema_contains_required_fields(self) -> None:
        path = Path("load_simulator/config/schema.py")
        text = path.read_text(encoding="utf-8")
        self.assertIn('mode: Literal["single", "mixed", "stress", "soak"]', text)
        self.assertIn("bottleneck_analysis: bool = True", text)
        self.assertIn("prompt_pool_size: int = 100", text)
        self.assertIn("duration_seconds: int = 60", text)
        self.assertIn("class AdaptiveRulesConfig", text)
        self.assertIn("adaptive_rules: AdaptiveRulesConfig", text)
        self.assertIn("class GlobalConfig", text)
        self.assertIn("class ChannelConfig", text)
        self.assertIn("class SystemCapacityConfig", text)

    def test_schema_has_legacy_alias_compat(self) -> None:
        text = Path("load_simulator/config/schema.py").read_text(encoding="utf-8")
        self.assertIn("llamafactory_url", text)
        self.assertIn("_compat_llamafactory_url", text)


if __name__ == "__main__":
    unittest.main()
