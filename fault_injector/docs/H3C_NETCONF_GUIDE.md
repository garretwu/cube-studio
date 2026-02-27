# H3C Comware 9 NETCONF Development Guide
> For AI coding agents writing management functions for H3C switches via ncclient

---

## Device Context

| Field | Value |
|---|---|
| Device | H3C S9855-24B8D |
| OS | Comware 9, Version 9.1.058 Release 9323 |
| Protocol | NETCONF over SSH, port 830 |
| Auth | Local user, service-type SSH |
| ncclient device_params | `{"name": "h3c"}` |

---

## Critical Rules (Read Before Writing Any Code)

### 1. Two Separate Namespaces — Always
H3C splits every module into a **data** namespace (read) and a **config** namespace (write).
Never mix them.

```python
NS_IFMGR_DATA   = "http://www.h3c.com/netconf/data:1.0-Ifmgr"   # for get()
NS_IFMGR_CONFIG = "http://www.h3c.com/netconf/config:1.0-Ifmgr"  # for edit_config()
```

### 2. Filter Keys Are Different Between Data and Config
- **Data (get):** filter by `<IfIndex>` integer — confirmed working
- **Config (edit_config):** key is also `<IfIndex>` — confirmed from get-config schema
- `<Name>` and `<AbbreviatedName>` are **NOT** valid filter keys in subtree filters
- Never filter by `<AbbreviatedName>` (e.g. `200GE1/0/4`) in XML — always resolve to `IfIndex` first

### 3. Always Inspect get-config Before Writing edit-config
The config schema only exposes a **subset** of fields. Run this before implementing any write operation:

```python
raw = client.get_config(f'<ModuleName xmlns="{NS_MODULE_CONFIG}"><Container/></ModuleName>')
print(client.pretty_print(raw))
```

This shows you exactly which tags and keys the config namespace accepts.

### 4. Integer Values, Not Strings
H3C YANG models use integers for state values — not human-readable strings:

| Field | Values |
|---|---|
| `AdminStatus` | `1` = UP, `2` = DOWN |
| `OperStatus` | `1` = UP, `2` = DOWN |
| `ActualDuplex` | `1` = Full, `2` = Half, `3` = Auto |
| `LinkType` | `1` = Access, `2` = Trunk, `3` = Hybrid |

Always map integers → strings in your parser. Never return raw `"1"` to callers.

### 5. ncclient get() vs get_config()
- `get()` → operational/state data (what is happening now)
- `get_config(source='running')` → configuration data (what is configured)
- Some fields only exist in one or the other

---

## Project Structure

```
h3c_netconf/
├── __init__.py
├── core/
│   ├── __init__.py
│   └── client.py          # H3CNetconfClient, H3CDevice
└── modules/
    ├── __init__.py
    ├── interface.py        # InterfaceModule  ← already implemented
    ├── vlan.py             # VlanModule       ← example to add
    └── bgp.py              # BgpModule        ← example to add
```

Each module follows identical structure. See Interface Module as the reference implementation.

---

## Connection Pattern

Always use as context manager. Never leave sessions open.

```python
from h3c_netconf.core.client import H3CNetconfClient, H3CDevice

DEVICE = H3CDevice(
    host="10.11.8.52",
    username="apitest",
    password="ApiTest@2026",
    port=830,
)

with H3CNetconfClient(DEVICE) as client:
    # do work here
    pass
```

The `H3CNetconfClient` constructor accepts `H3CDevice` and internally calls:
```python
manager.connect(..., device_params={"name": "h3c"})
```
The `device_params={"name": "h3c"}` is **required** — without it ncclient sends incorrect RPC framing.

---

## Writing a New Module — Step by Step

### Step 1: Find the Namespace

Check the capabilities list. Every H3C module appears as two URLs:
```
http://www.h3c.com/netconf/data:1.0-VLAN     ← read
http://www.h3c.com/netconf/config:1.0-VLAN   ← write
```

The suffix after the `-` is the module name (e.g. `VLAN`, `BGP`, `LLDP`, `ARP`).

### Step 2: Discover the Data Schema

```python
with H3CNetconfClient(DEVICE) as client:
    NS_DATA = "http://www.h3c.com/netconf/data:1.0-VLAN"
    raw = client.get(f'<VLAN xmlns="{NS_DATA}"/>')
    print(client.pretty_print(raw))
```

Read the XML output carefully. Note:
- Top-level container tag (e.g. `<VLAN>`, `<Ifmgr>`, `<BGP>`)
- List container tag (e.g. `<VLANs>`, `<Interfaces>`)
- Item tag (e.g. `<VLANID>`, `<Interface>`)
- Key field (e.g. `<ID>`, `<IfIndex>`, `<VrfIndex>`)
- Value fields and their types (integer codes vs strings)

### Step 3: Discover the Config Schema

```python
with H3CNetconfClient(DEVICE) as client:
    NS_CONFIG = "http://www.h3c.com/netconf/config:1.0-VLAN"
    raw = client.get_config(f'<VLAN xmlns="{NS_CONFIG}"/>')
    print(client.pretty_print(raw))
```

Compare with the data schema. The config schema will have fewer fields — only the ones you can set.

### Step 4: Implement the Module

```python
# modules/vlan.py  — example template
from dataclasses import dataclass
from typing import Optional
import xml.etree.ElementTree as ET
from ..core.client import H3CNetconfClient

NS_VLAN_DATA   = "http://www.h3c.com/netconf/data:1.0-VLAN"
NS_VLAN_CONFIG = "http://www.h3c.com/netconf/config:1.0-VLAN"

# Define integer mappings after inspecting real device output
_VLAN_STATUS_MAP = {"1": "Active", "2": "Suspend"}


@dataclass
class VlanStatus:
    vlan_id: int
    name: Optional[str] = None
    status: Optional[str] = None
    description: Optional[str] = None


class VlanModule:
    def __init__(self, client: H3CNetconfClient):
        self.client = client

    def get_all_vlans(self) -> list[VlanStatus]:
        filter_xml = f'<VLAN xmlns="{NS_VLAN_DATA}"><VLANs/></VLAN>'
        raw = self.client.get(filter_xml)
        return self._parse_vlans(raw)

    def get_vlan(self, vlan_id: int) -> Optional[VlanStatus]:
        filter_xml = f"""
        <VLAN xmlns="{NS_VLAN_DATA}">
          <VLANs>
            <VLANID>
              <ID>{vlan_id}</ID>
            </VLANID>
          </VLANs>
        </VLAN>"""
        raw = self.client.get(filter_xml)
        results = self._parse_vlans(raw)
        return results[0] if results else None

    def create_vlan(self, vlan_id: int, name: str) -> bool:
        config_xml = f"""
        <config>
          <VLAN xmlns="{NS_VLAN_CONFIG}">
            <VLANs>
              <VLANID>
                <ID>{vlan_id}</ID>
                <Name>{name}</Name>
              </VLANID>
            </VLANs>
          </VLAN>
        </config>"""
        reply = self.client.edit_config(config_xml)
        return self._is_ok(reply)

    def delete_vlan(self, vlan_id: int) -> bool:
        config_xml = f"""
        <config>
          <VLAN xmlns="{NS_VLAN_CONFIG}">
            <VLANs>
              <VLANID operation="remove">
                <ID>{vlan_id}</ID>
              </VLANID>
            </VLANs>
          </VLAN>
        </config>"""
        reply = self.client.edit_config(config_xml)
        return self._is_ok(reply)

    @staticmethod
    def _is_ok(reply: str) -> bool:
        return "<ok/>" in reply or "<ok />" in reply

    def _parse_vlans(self, raw_xml: str) -> list[VlanStatus]:
        root = ET.fromstring(raw_xml)
        vlans = []
        for elem in root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag not in ("VLANID", "VLAN"):   # adjust after inspecting real output
                continue
            vlan_id = None
            name = None
            status = None
            for child in elem:
                t = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                v = (child.text or "").strip()
                if   t == "ID":     vlan_id = int(v) if v.isdigit() else None
                elif t == "Name":   name    = v
                elif t == "Status": status  = _VLAN_STATUS_MAP.get(v, v)
            if vlan_id:
                vlans.append(VlanStatus(vlan_id=vlan_id, name=name, status=status))
        return vlans
```

---

## XML Patterns Reference

### Subtree Filter — Get Single Item by Key
```xml
<ModuleName xmlns="http://www.h3c.com/netconf/data:1.0-MODULE">
  <Container>
    <Item>
      <KeyField>value</KeyField>
    </Item>
  </Container>
</ModuleName>
```

### Subtree Filter — Get All Items
```xml
<ModuleName xmlns="http://www.h3c.com/netconf/data:1.0-MODULE">
  <Container/>
</ModuleName>
```

### Edit Config — Create or Modify
```xml
<config>
  <ModuleName xmlns="http://www.h3c.com/netconf/config:1.0-MODULE">
    <Container>
      <Item>
        <KeyField>value</KeyField>
        <FieldToSet>new_value</FieldToSet>
      </Item>
    </Container>
  </ModuleName>
</config>
```

### Edit Config — Delete Item
Add `operation="remove"` on the item element:
```xml
<config>
  <ModuleName xmlns="http://www.h3c.com/netconf/config:1.0-MODULE">
    <Container>
      <Item operation="remove">
        <KeyField>value</KeyField>
      </Item>
    </Container>
  </ModuleName>
</config>
```

### Edit Config — Remove a Single Field (restore to default)
```xml
<FieldName operation="remove"/>
```

---

## Interface Module — Confirmed Working Reference

Key facts about the interface module confirmed from real device:

```
Data namespace  : http://www.h3c.com/netconf/data:1.0-Ifmgr
Config namespace: http://www.h3c.com/netconf/config:1.0-Ifmgr
Top container   : <Ifmgr>
List container  : <Interfaces>
Item tag        : <Interface>
Data key        : <IfIndex>   (integer, e.g. 4 for 200GE1/0/4)
Config key      : <IfIndex>   (same)
```

Confirmed data tags:

| XML Tag | Type | Notes |
|---|---|---|
| `IfIndex` | int | Unique interface index, use as key |
| `Name` | string | Full name: `TwoHundredGigE1/0/4` |
| `AbbreviatedName` | string | Short name: `200GE1/0/4` |
| `AdminStatus` | int | `1`=UP `2`=DOWN |
| `OperStatus` | int | `1`=UP `2`=DOWN |
| `Description` | string | Interface description |
| `ActualSpeed` | int | Speed in bps |
| `ActualDuplex` | int | `1`=Full `2`=Half `3`=Auto |
| `MAC` | string | Format: `2C-4C-7D-AA-AF-00` |
| `PVID` | int | Native/access VLAN |
| `LinkType` | int | `1`=Access `2`=Trunk `3`=Hybrid |
| `LastChange` | string | e.g. `29Day19Hour49Minute50Second` |

Confirmed config tags (from get-config):

| XML Tag | Type | Notes |
|---|---|---|
| `IfIndex` | int | **Key field** — required in all config RPCs |
| `LinkType` | int | `1`=Access `2`=Trunk `3`=Hybrid |
| `PVID` | int | Native VLAN |
| `AdminStatus` | int | `1`=UP `2`=DOWN |
| `Description` | string | Interface description |

**Important:** `Name` and `AbbreviatedName` do **not** appear in the config schema.
Always resolve abbreviated name → `IfIndex` before any write operation:

```python
def _resolve_if_index(self, abbreviated_name: str) -> int:
    status = self.get_interface_status(abbreviated_name)
    if status.if_index is None:
        raise ValueError(f"Could not resolve IfIndex for '{abbreviated_name}'")
    return status.if_index
```

---

## Available Modules on This Device

All of the following are confirmed in the device capabilities. Each follows the same pattern.
Implement by discovering the schema with get() and get_config() as described above.

### High Priority
| Module Suffix | Capabilities Key | Purpose |
|---|---|---|
| `VLAN` | `H3C-vlan-data` | VLAN create/delete/query |
| `ETH` | `H3C-eth-data` | Ethernet port config (speed, duplex, flow control) |
| `LLDP` | `H3C-lldp-data` | LLDP neighbors |
| `ARP` | `H3C-arp-data` | ARP table |
| `MAC` | `H3C-mac-data` | MAC address table |
| `STP` | `H3C-stp-data` | Spanning tree |
| `LAGG` | `H3C-lagg-data` | Link aggregation / LAG |

### Routing
| Module Suffix | Purpose |
|---|---|
| `Route` | Routing table |
| `StaticRoute` | Static routes |
| `OSPF` | OSPF config and state |
| `BGP` | BGP neighbors and routes |
| `ISIS` | IS-IS |

### Monitoring
| Module Suffix | Purpose |
|---|---|
| `ResourceMonitor` | CPU, memory utilization |
| `Device` | System info, uptime, temperature |
| `Syslog` | Syslog config |
| `SNMP` | SNMP config |
| `NQA` | Network quality analysis |
| `BFD` | BFD sessions |

### Advanced
| Module Suffix | Purpose |
|---|---|
| `EVPN` | EVPN config |
| `VXLAN` | VXLAN config |
| `L3vpn` | L3 VPN (VRF) |
| `MPLS` | MPLS config |
| `PFC` | Priority flow control |
| `MQC` | QoS policy |

---

## XML Parsing Pattern

All modules use the same namespace-stripping parser pattern:

```python
def _parse_items(self, raw_xml: str) -> list[MyDataclass]:
    root = ET.fromstring(raw_xml)
    results = []

    for elem in root.iter():
        # Strip namespace prefix from tag
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        if tag != "ItemTag":          # replace with actual item element name
            continue

        obj = MyDataclass()
        for child in elem:
            t = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            v = (child.text or "").strip()

            if   t == "KeyField":   obj.key   = int(v) if v.isdigit() else None
            elif t == "TextField":  obj.text  = v
            elif t == "StatusInt":  obj.state = STATUS_MAP.get(v, v)

        if obj.key:                   # only append if key field was found
            results.append(obj)

    return results
```

---

## Testing Pattern

Each module must have unit tests using mocks — no real device needed.

```python
# tests/test_vlan.py
import unittest
from unittest.mock import MagicMock
from h3c_netconf.core.client import H3CNetconfClient
from h3c_netconf.modules.vlan import VlanModule

MOCK_VLAN_REPLY = """<?xml version="1.0"?>
<rpc-reply xmlns="urn:ietf:params:xml:ns:netconf:base:1.0">
  <data>
    <VLAN xmlns="http://www.h3c.com/netconf/data:1.0-VLAN">
      <VLANs>
        <VLANID>
          <ID>10</ID>
          <Name>Management</Name>
          <Status>1</Status>
        </VLANID>
      </VLANs>
    </VLAN>
  </data>
</rpc-reply>"""

MOCK_OK = '<rpc-reply><ok/></rpc-reply>'

class TestVlanModule(unittest.TestCase):
    def _make_module(self):
        client = MagicMock(spec=H3CNetconfClient)
        return VlanModule(client), client

    def test_get_all_vlans_parses_correctly(self):
        module, client = self._make_module()
        client.get.return_value = MOCK_VLAN_REPLY
        vlans = module.get_all_vlans()
        self.assertEqual(len(vlans), 1)
        self.assertEqual(vlans[0].vlan_id, 10)
        self.assertEqual(vlans[0].name, "Management")

    def test_create_vlan_sends_correct_xml(self):
        module, client = self._make_module()
        client.edit_config.return_value = MOCK_OK
        result = module.create_vlan(100, "TestVlan")
        self.assertTrue(result)
        xml_sent = client.edit_config.call_args[0][0]
        self.assertIn("100", xml_sent)
        self.assertIn("TestVlan", xml_sent)

    def test_delete_vlan_uses_remove_operation(self):
        module, client = self._make_module()
        client.edit_config.return_value = MOCK_OK
        module.delete_vlan(100)
        xml_sent = client.edit_config.call_args[0][0]
        self.assertIn('operation="remove"', xml_sent)

if __name__ == "__main__":
    unittest.main(verbosity=2)
```

Run all tests (no device needed):
```bash
python -m unittest discover -s h3c_netconf/tests -p "test_*.py" -v
```

---

## Common Errors and Fixes

| Error | Cause | Fix |
|---|---|---|
| `Unexpected element 'ns':'tagname'` | Wrong tag name or namespace | Run get_config() to inspect real schema, check tag name |
| `Unexpected element 'ns':'n'` | Used `<n>` as name key in config | Use `<IfIndex>` as key instead |
| `ModuleNotFoundError: No module named 'h3c_netconf'` | Script not in parent of package dir | Move script up one level so `h3c_netconf/` folder is a sibling |
| Fields all `None` after parsing | Tag names wrong in parser | Print raw XML first, check actual tag names from device |
| `RPCError: filter` under get | Passed `<filter>` wrapper to `client.get()` | Pass only the content inside filter, not the wrapper tag |
| SSH negotiation fails | OpenSSH rejects `ssh-rsa` | Add `-o HostKeyAlgorithms=ssh-rsa` or use ncclient directly |

---

## Quick Debug Snippet

When a module isn't working, always start with raw XML inspection:

```python
with H3CNetconfClient(DEVICE) as client:
    # 1. See what data is available
    NS = "http://www.h3c.com/netconf/data:1.0-MODULENAME"
    print("=== DATA ===")
    print(client.pretty_print(client.get(f'<MODULENAME xmlns="{NS}"/>')))

    # 2. See what config fields exist
    NS_C = "http://www.h3c.com/netconf/config:1.0-MODULENAME"
    print("=== CONFIG ===")
    print(client.pretty_print(client.get_config(f'<MODULENAME xmlns="{NS_C}"/>')))
```

Replace `MODULENAME` with the module you're working on (e.g. `VLAN`, `BGP`, `ARP`).
