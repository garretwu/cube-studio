from __future__ import annotations

import unittest

from load_simulator.load.profile import LoadProfile, Stage


class LoadProfileTests(unittest.TestCase):
    def test_constant_profile(self) -> None:
        profile = LoadProfile.constant(value=12, duration_seconds=5)
        self.assertEqual(profile.value_at(0), 12.0)
        self.assertEqual(profile.value_at(4), 12.0)
        self.assertEqual(len(profile.timeline()), 5)

    def test_stepped_profile(self) -> None:
        profile = LoadProfile.stepped([Stage(value=1, duration_seconds=2), Stage(value=5, duration_seconds=3)])
        self.assertEqual(profile.value_at(0), 1.0)
        self.assertEqual(profile.value_at(1), 1.0)
        self.assertEqual(profile.value_at(2), 5.0)
        self.assertEqual(profile.value_at(10), 5.0)

    def test_spike_profile(self) -> None:
        profile = LoadProfile.spike(
            base_value=2,
            spike_value=20,
            spike_start_second=3,
            spike_duration_seconds=2,
            total_duration_seconds=8,
        )
        self.assertEqual(profile.value_at(1), 2.0)
        self.assertEqual(profile.value_at(3), 20.0)
        self.assertEqual(profile.value_at(4), 20.0)
        self.assertEqual(profile.value_at(5), 2.0)


if __name__ == "__main__":
    unittest.main()
