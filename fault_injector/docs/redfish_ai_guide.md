# Redfish AI Usage Guide

## Purpose

This guide explains how AI agents should use `fault_injector/channels/redfish.py` safely and consistently.

Primary goals:

1. Read BMC info first
2. Discover supported Redfish operations on the target BMC
3. Execute only allowed and recoverable operations

## Channel Design (Scalable)

`RedfishChannel` is built with:

- session management (`authenticate`, `logout`, token cache)
- generic request entry (`request`)
- discovery APIs (`get_bmc_info`, `discover_capabilities`)
- operation-specific helpers (`get_thermal`, `get_power`, `get_sensors`, `reset_system`, `set_fan_control`)
- safety checks for forbidden paths and reset types
- connection reuse with per-host async HTTP clients

Use this order in agents/workflows:

1. `authenticate(...)`
2. `get_bmc_info(...)`
3. `discover_capabilities(...)`
4. perform required operation via helper or `request(...)`
5. `logout(...)` (optional but recommended)

## Example Usage

```python
from lib.channels.redfish import RedfishChannel

channel = RedfishChannel(dry_run=False, timeout=30)

auth = await channel.authenticate(
    bmc_host="10.11.8.13",
    username="admin",
    password="***",
    verify_tls=False,
)
if not auth.success:
    raise RuntimeError(auth.error)

info = await channel.get_bmc_info("10.11.8.13", verify_tls=False)
caps = await channel.discover_capabilities("10.11.8.13", verify_tls=False)

thermal = await channel.get_thermal("10.11.8.13", verify_tls=False)
```

## Capability Discovery Result (Observed)

Observed on `https://10.11.8.13` (queried on 2026-02-27):

- Redfish service root reachable
- Core services present:
  - `Systems`
  - `Managers`
  - `Chassis`
  - `SessionService`
  - `AccountService`
  - `UpdateService`
  - `TelemetryService`
  - `EventService`
  - `Registries`
- System actions:
  - `#ComputerSystem.Reset`
- Manager actions:
  - `#Manager.Reset`
  - `#Manager.ResetToDefaults`
- Chassis actions:
  - `#Chassis.Reset`
- Update actions:
  - `#UpdateService.SimpleUpdate`
- Telemetry actions:
  - `#TelemetryService.SubmitTestMetricReport`
- Event actions:
  - `#EventService.SubmitTestEvent`
- Sensor endpoints confirmed:
  - `/redfish/v1/Chassis/Self/Thermal`
  - `/redfish/v1/Chassis/Self/Power`
  - `/redfish/v1/Chassis/Self/Sensors`

## Safety Rules for AI

Always:

- authenticate first and check `ChannelResult.success`
- use `discover_capabilities` before invoking non-read operations
- prefer read operations unless fault injection explicitly requires write/reset
- record/associate `fault_id` for operations that need rollback traceability

Never:

- call factory reset paths
- modify management NIC/network paths blindly
- use aggressive reset types (`ForceOff`, `ForceRestart`) unless explicitly approved and guard rules allow

## Testing Expectations

Tests for this channel are in:

- `fault_injector/tests/unit/features/channels/test_redfish_channel.py`
- `fault_injector/tests/fixtures/redfish_fixture.py`

Coverage includes:

- BMC info retrieval
- capability discovery parsing
- auth token flow
- missing-auth error path
- safety-sensitive path/reset blocking
