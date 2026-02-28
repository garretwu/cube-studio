"""
Tests for SwitchChannel.
"""
from __future__ import annotations

import pytest

from lib.channels.switch import SwitchChannel
from fault_injector.tests.fixtures.switch_fixture import (
    MOCK_INTERFACES,
    SAMPLE_INTERFACE_XML,
    SWITCH_DEVICES,
)


class _MockWal:
    def __init__(self):
        self.records = []
        self.recovered = []

    def record(self, **kwargs):
        self.records.append(kwargs)

    def mark_recovered(self, fault_id: str):
        self.recovered.append(fault_id)


class _MockNetconfClient:
    def __init__(self, edit_ok: bool = True):
        self.edit_ok = edit_ok
        self.last_config = ""

    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def get(self, _filter_xml: str) -> str:
        return SAMPLE_INTERFACE_XML

    def edit_config(self, config_xml: str, target: str = "running") -> bool:
        _ = target
        self.last_config = config_xml
        return self.edit_ok


class TestSwitchChannelUnit:
    def test_should_parse_interface_fields_when_xml_is_valid(self):
        channel = SwitchChannel(devices=SWITCH_DEVICES, dry_run=True)
        interfaces = channel._parse_interfaces(SAMPLE_INTERFACE_XML)

        assert len(interfaces) == 1
        iface = interfaces[0]
        assert iface.if_index == 4
        assert iface.abbreviated_name == "GE1/0/4"
        assert iface.admin_status == "up"
        assert iface.oper_status == "down"
        assert iface.actual_duplex == "full"
        assert iface.link_type == "trunk"

    def test_should_return_empty_list_when_xml_is_invalid(self):
        channel = SwitchChannel(devices=SWITCH_DEVICES, dry_run=True)
        interfaces = channel._parse_interfaces("<broken>")
        assert interfaces == []

    def test_should_return_mock_status_when_get_interface_by_index_in_dry_run(self):
        channel = SwitchChannel(devices=SWITCH_DEVICES, dry_run=True)
        status = channel.get_interface_by_index("sw1", 4)

        assert status is not None
        assert status.if_index == 4
        assert status.admin_status == "up"
        assert status.oper_status == "up"

    def test_should_record_wal_and_return_dry_run_when_shutdown_port_with_fault_id(self):
        wal = _MockWal()
        channel = SwitchChannel(devices=SWITCH_DEVICES, dry_run=True, wal=wal)
        channel._resolve_if_index = lambda switch, interface: 4  # type: ignore[method-assign]

        result = channel.shutdown_port("sw1", "GE1/0/4", fault_id="f-1")

        assert result.success is True
        assert result.dry_run is True
        assert len(wal.records) == 1
        assert wal.records[0]["recover_action"] == "bringup_port"

    def test_should_return_error_when_shutdown_port_ifindex_cannot_be_resolved(self):
        channel = SwitchChannel(devices=SWITCH_DEVICES, dry_run=True)
        channel._resolve_if_index = lambda switch, interface: None  # type: ignore[method-assign]

        result = channel.shutdown_port("sw1", "GE1/0/404")

        assert result.success is False
        assert "IfIndex" in result.error

    def test_should_mark_wal_recovered_when_bringup_port_with_fault_id(self):
        wal = _MockWal()
        channel = SwitchChannel(devices=SWITCH_DEVICES, dry_run=True, wal=wal)
        channel._resolve_if_index = lambda switch, interface: 4  # type: ignore[method-assign]

        result = channel.bringup_port("sw1", "GE1/0/4", fault_id="f-2")

        assert result.success is True
        assert result.dry_run is True
        assert wal.recovered == ["f-2"]

    def test_should_resolve_ifindex_when_interface_exists(self):
        channel = SwitchChannel(devices=SWITCH_DEVICES, dry_run=False)
        channel.get_all_interfaces = lambda switch: MOCK_INTERFACES  # type: ignore[method-assign]

        if_index = channel._resolve_if_index("sw1", "GE1/0/5")

        assert if_index == 5

    def test_should_return_failure_when_edit_config_fails_on_shutdown(self):
        channel = SwitchChannel(devices=SWITCH_DEVICES, dry_run=False)
        channel._resolve_if_index = lambda switch, interface: 4  # type: ignore[method-assign]
        channel._clients["sw1"] = _MockNetconfClient(edit_ok=False)

        result = channel.shutdown_port("sw1", "GE1/0/4")

        assert result.success is False
        assert "edit-config" in result.error

    def test_should_return_true_when_test_connection_in_dry_run(self):
        channel = SwitchChannel(devices=SWITCH_DEVICES, dry_run=True)
        assert channel.test_connection("sw1") is True

    @pytest.mark.asyncio
    async def test_should_return_failure_when_execute_impl_action_unknown(self):
        channel = SwitchChannel(devices=SWITCH_DEVICES, dry_run=True)
        result = await channel._execute_impl("unknown", {})

        assert result.success is False
        assert "Unknown action" in result.error
