from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from lib.fchannels.switch import SwitchChannel

pytestmark = [
    pytest.mark.live_netconf,
    pytest.mark.skipif(
        os.getenv("FAULT_INJECTOR_LIVE_NETCONF") != "1",
        reason="Set FAULT_INJECTOR_LIVE_NETCONF=1 to enable live NETCONF tests.",
    ),
]


def _load_switch_fixture() -> dict:
    fixture_path = Path(__file__).resolve().parents[1] / "fixtures" / "switch_config.yaml"
    if not fixture_path.exists():
        pytest.skip(f"Missing switch fixture: {fixture_path}")
    with open(fixture_path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _live_channel_and_target() -> tuple[SwitchChannel, str, str]:
    config = _load_switch_fixture()
    switches = config.get("switches", {})
    interfaces = config.get("test_interfaces", [])

    if not switches:
        pytest.skip("No switches configured in switch_config.yaml")
    if not interfaces:
        pytest.skip("No dedicated test interfaces configured in switch_config.yaml")

    switch_name = list(switches.keys())[0]
    interface_name = interfaces[0]["name"]
    channel = SwitchChannel(devices=switches, dry_run=False)
    return channel, switch_name, interface_name


def test_live_netconf_connection_smoke():
    channel, switch_name, _ = _live_channel_and_target()
    try:
        assert channel.test_connection(switch_name) is True
    finally:
        channel.close()


def test_live_netconf_read_only_interface_status():
    channel, switch_name, interface_name = _live_channel_and_target()
    try:
        status = channel.get_interface_status(switch_name, interface_name)
        assert status is not None
        assert status.if_index > 0
        assert status.abbreviated_name != ""
    finally:
        channel.close()


def test_live_netconf_shutdown_bringup_reversible_cycle():
    channel, switch_name, interface_name = _live_channel_and_target()
    try:
        down = channel.shutdown_port(switch=switch_name, interface=interface_name)
        assert down.success is True

        assert channel.verify_admin_state(switch_name, interface_name, "down") is True

        up = channel.bringup_port(switch=switch_name, interface=interface_name)
        assert up.success is True

        assert channel.verify_admin_state(switch_name, interface_name, "up") is True
    finally:
        channel.close()
