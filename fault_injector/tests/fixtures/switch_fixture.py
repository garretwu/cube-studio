"""
Shared Switch channel fixtures.
"""

from __future__ import annotations

from lib.channels.switch import InterfaceStatus

SWITCH_DEVICES = {
    "sw1": {
        "host": "10.0.0.1",
        "port": 830,
        "username": "admin",
        "password": "pwd",
    }
}

SAMPLE_INTERFACE_XML = """
<rpc-reply>
  <data>
    <Ifmgr xmlns="http://www.h3c.com/netconf/data:1.0-Ifmgr">
      <Interfaces>
        <Interface>
          <IfIndex>4</IfIndex>
          <Name>GigabitEthernet1/0/4</Name>
          <AbbreviatedName>GE1/0/4</AbbreviatedName>
          <AdminStatus>1</AdminStatus>
          <OperStatus>2</OperStatus>
          <Description>uplink</Description>
          <ActualSpeed>1000</ActualSpeed>
          <ActualDuplex>1</ActualDuplex>
          <MAC>00:11:22:33:44:55</MAC>
          <PVID>10</PVID>
          <LinkType>2</LinkType>
        </Interface>
      </Interfaces>
    </Ifmgr>
  </data>
</rpc-reply>
"""

MOCK_INTERFACES = [
    InterfaceStatus(if_index=4, abbreviated_name="GE1/0/4", admin_status="up", oper_status="up"),
    InterfaceStatus(if_index=5, abbreviated_name="GE1/0/5", admin_status="up", oper_status="up"),
]
