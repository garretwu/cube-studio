"""
Tests for RedfishChannel.
"""
from __future__ import annotations

import json

import pytest

from lib.fchannels.redfish import RedfishChannel
from fault_injector.tests.fixtures.redfish_fixture import (
    CHASSIS_COLLECTION,
    CHASSIS_SELF,
    MANAGER_SELF,
    MANAGERS_COLLECTION,
    SERVICE_ROOT,
    SYSTEM_SELF,
    SYSTEMS_COLLECTION,
    TEST_BMC_HOST,
    TEST_PASSWORD,
    TEST_SESSION_LOCATION,
    TEST_TOKEN,
    TEST_USERNAME,
    THERMAL_PAYLOAD,
    UPDATE_SERVICE,
)


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, headers: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}
        self.text = json.dumps(self._payload) if self._payload else ""

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    def __init__(self, routes: dict[tuple[str, str], FakeResponse]):
        self.routes = routes
        self.last_headers: dict | None = None
        self.last_method: str | None = None
        self.last_url: str | None = None
        self.last_json: dict | None = None
        self.last_data: dict | None = None

    async def request(self, method: str, url: str, headers=None, json=None, params=None, data=None):
        self.last_headers = headers or {}
        self.last_method = method.upper()
        self.last_url = url
        self.last_json = json
        self.last_data = data
        key = (method.upper(), url)
        if key not in self.routes:
            return FakeResponse(404, {"error": "not found"})
        return self.routes[key]

    async def post(self, url: str, json=None, headers=None, data=None):
        return await self.request("POST", url, headers=headers, json=json, data=data)

    async def delete(self, url: str, headers=None):
        return await self.request("DELETE", url, headers=headers)

    async def aclose(self):
        return None


class TestRedfishChannelInfo:
    @pytest.fixture
    def redfish_channel(self) -> RedfishChannel:
        return RedfishChannel(dry_run=False, wal=None, guard=None)

    @pytest.mark.asyncio
    async def test_should_return_bmc_info_when_service_root_and_collections_exist(self, redfish_channel):
        routes = {
            ("GET", "/redfish/v1/"): FakeResponse(200, SERVICE_ROOT),
            ("GET", "/redfish/v1/Managers"): FakeResponse(200, MANAGERS_COLLECTION),
            ("GET", "/redfish/v1/Managers/Self"): FakeResponse(200, MANAGER_SELF),
            ("GET", "/redfish/v1/Systems"): FakeResponse(200, SYSTEMS_COLLECTION),
            ("GET", "/redfish/v1/Systems/Self"): FakeResponse(200, SYSTEM_SELF),
            ("GET", "/redfish/v1/Chassis"): FakeResponse(200, CHASSIS_COLLECTION),
            ("GET", "/redfish/v1/Chassis/Self"): FakeResponse(200, CHASSIS_SELF),
        }
        fake_client = FakeClient(routes)
        redfish_channel._tokens[TEST_BMC_HOST] = TEST_TOKEN
        async def _mock_get_client(*args, **kwargs):
            return fake_client
        redfish_channel._get_client = _mock_get_client  # type: ignore[method-assign]

        result = await redfish_channel.get_bmc_info(TEST_BMC_HOST, verify_tls=False)

        assert result.success is True
        payload = json.loads(result.output)
        assert payload["service_root"]["redfish_version"] == SERVICE_ROOT["RedfishVersion"]
        assert payload["manager"]["firmware_version"] == MANAGER_SELF["FirmwareVersion"]
        assert payload["system"]["allowable_reset_types"] == ["On", "ForceRestart", "GracefulRestart"]
        assert payload["chassis"]["name"] == CHASSIS_SELF["Name"]

    @pytest.mark.asyncio
    async def test_should_discover_actions_when_services_and_members_expose_actions(self, redfish_channel):
        routes = {
            ("GET", "/redfish/v1/"): FakeResponse(200, SERVICE_ROOT),
            ("GET", "/redfish/v1/Managers"): FakeResponse(200, MANAGERS_COLLECTION),
            ("GET", "/redfish/v1/Managers/Self"): FakeResponse(200, MANAGER_SELF),
            ("GET", "/redfish/v1/Systems"): FakeResponse(200, SYSTEMS_COLLECTION),
            ("GET", "/redfish/v1/Systems/Self"): FakeResponse(200, SYSTEM_SELF),
            ("GET", "/redfish/v1/Chassis"): FakeResponse(200, CHASSIS_COLLECTION),
            ("GET", "/redfish/v1/Chassis/Self"): FakeResponse(200, CHASSIS_SELF),
            ("GET", "/redfish/v1/UpdateService"): FakeResponse(200, UPDATE_SERVICE),
        }
        fake_client = FakeClient(routes)
        redfish_channel._tokens[TEST_BMC_HOST] = TEST_TOKEN
        async def _mock_get_client(*args, **kwargs):
            return fake_client
        redfish_channel._get_client = _mock_get_client  # type: ignore[method-assign]

        result = await redfish_channel.discover_capabilities(TEST_BMC_HOST, verify_tls=False)

        assert result.success is True
        payload = json.loads(result.output)
        assert payload["services"]["Systems"]["members_count"] == 1
        assert payload["services"]["Systems"]["members"][0]["actions"] == ["#ComputerSystem.Reset"]
        assert payload["services"]["UpdateService"]["actions"] == ["#UpdateService.SimpleUpdate"]

    @pytest.mark.asyncio
    async def test_should_fail_when_auth_token_missing_for_authenticated_query(self, redfish_channel):
        routes = {
            ("GET", "/redfish/v1/"): FakeResponse(200, SERVICE_ROOT),
        }
        fake_client = FakeClient(routes)
        async def _mock_get_client(*args, **kwargs):
            return fake_client
        redfish_channel._get_client = _mock_get_client  # type: ignore[method-assign]

        result = await redfish_channel.get_service_root(TEST_BMC_HOST, verify_tls=False)

        assert result.success is False
        assert "Missing Redfish auth token" in result.error


class TestRedfishChannelAuthAndSafety:
    @pytest.fixture
    def redfish_channel(self) -> RedfishChannel:
        return RedfishChannel(dry_run=False, wal=None, guard=object())

    @pytest.mark.asyncio
    async def test_should_store_token_when_authenticate_succeeds(self, redfish_channel):
        routes = {
            ("POST", "/redfish/v1/SessionService/Sessions"): FakeResponse(
                201,
                {},
                headers={"X-Auth-Token": TEST_TOKEN, "Location": TEST_SESSION_LOCATION},
            ),
            ("GET", "/redfish/v1/Chassis/Self/Thermal"): FakeResponse(200, THERMAL_PAYLOAD),
        }
        fake_client = FakeClient(routes)
        async def _mock_get_client(*args, **kwargs):
            return fake_client
        redfish_channel._get_client = _mock_get_client  # type: ignore[method-assign]

        auth = await redfish_channel.authenticate(TEST_BMC_HOST, TEST_USERNAME, TEST_PASSWORD, verify_tls=False)
        thermal = await redfish_channel.get_thermal(TEST_BMC_HOST, verify_tls=False)

        assert auth.success is True
        assert redfish_channel._tokens[TEST_BMC_HOST] == TEST_TOKEN
        assert thermal.success is True
        assert fake_client.last_headers == {"X-Auth-Token": TEST_TOKEN}

    @pytest.mark.asyncio
    async def test_should_use_web_fan_status_endpoints_for_fan_control(self, redfish_channel):
        routes = {
            ("POST", "/api/session"): FakeResponse(200, {"ok": 0, "CSRFToken": "mock-csrf-token"}),
            ("GET", "/api/fan-status"): FakeResponse(200, {"fans": [{"id": 0, "mode": "Manual", "pwm": 30}]}),
            ("POST", "/api/actions/fan-status"): FakeResponse(200, {"ok": True}),
        }
        fake_client = FakeClient(routes)
        redfish_channel.set_web_credentials(TEST_BMC_HOST, TEST_USERNAME, TEST_PASSWORD)

        async def _mock_get_client(*args, **kwargs):
            return fake_client

        redfish_channel._get_client = _mock_get_client  # type: ignore[method-assign]

        get_result = await redfish_channel.get_web_fan_status(TEST_BMC_HOST, verify_tls=False)
        assert get_result.success is True
        assert fake_client.last_method == "GET"
        assert fake_client.last_url == "/api/fan-status"
        assert fake_client.last_data is None
        assert fake_client.last_headers == {
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-CSRFTOKEN": "mock-csrf-token",
        }

        set_result = await redfish_channel.set_fan_control(
            bmc_host=TEST_BMC_HOST,
            fan_index=0,
            mode="Manual",
            pwm=30,
            verify_tls=False,
        )
        assert set_result.success is True
        assert fake_client.last_method == "POST"
        assert fake_client.last_url == "/api/actions/fan-status"
        assert fake_client.last_headers == {
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-CSRFTOKEN": "mock-csrf-token",
            "Content-Type": "application/json",
        }
        assert fake_client.last_json == {"fanMode": 1, "fanBpIndex": 255, "fanIndex": 0, "pwm": 30}

    @pytest.mark.asyncio
    async def test_should_block_forbidden_reset_type_when_guard_enabled(self, redfish_channel):
        result = await redfish_channel.reset_system(
            bmc_host=TEST_BMC_HOST,
            reset_type="ForceOff",
            verify_tls=False,
        )

        assert result.success is False
        assert "Forbidden reset type" in result.error

    @pytest.mark.asyncio
    async def test_should_block_forbidden_path_when_guard_enabled(self, redfish_channel):
        result = await redfish_channel.request(
            bmc_host=TEST_BMC_HOST,
            method="GET",
            path="/redfish/v1/Managers/Self/EthernetInterfaces",
            verify_tls=False,
            requires_auth=False,
        )

        assert result.success is False
        assert "Forbidden Redfish endpoint" in result.error
