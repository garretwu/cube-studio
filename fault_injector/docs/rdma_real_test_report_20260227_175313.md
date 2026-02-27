# RDMA Real Scenario Test Report

- Execution Time: 2026-02-27 17:53:13
- Switch: `Switch200GE` (`10.11.8.52:830`)
- Interface: `200GE1/0/4` (IfIndex=4)
- Host (F-5): `10.11.4.13` / `roce200`
- Result: **6/6 passed**
- Raw Log: `fault_injector/docs/rdma_real_test_log_20260227_175313.txt`

## F-1 PFC Deadlock - PASS

- Scenario ID: `pfc_deadlock`
- Inject: `True` | Error: `None`
- Recover: `True` | Error: `None`
- WAL Active Faults After Case: `0`

Baseline:
```json
{'admin': 'up', 'oper': 'up', 'description': 'wj-lab-cpt-04', 'pvid': 10, 'link_type': 'trunk'}
```
Post-Inject:
```json
{'admin': 'up', 'oper': 'up', 'description': '[fi:pfc_deadlock:rdma_f1_batch_001]', 'pvid': 10, 'link_type': 'trunk'}
```
Post-Recover:
```json
{'admin': 'up', 'oper': 'up', 'description': 'wj-lab-cpt-04', 'pvid': 10, 'link_type': 'trunk'}
```

## F-2 ECN Misconfiguration - PASS

- Scenario ID: `ecn_misconfiguration`
- Inject: `True` | Error: `None`
- Recover: `True` | Error: `None`
- WAL Active Faults After Case: `0`

Baseline:
```json
{'admin': 'up', 'oper': 'up', 'description': 'wj-lab-cpt-04', 'pvid': 10, 'link_type': 'trunk'}
```
Post-Inject:
```json
{'admin': 'up', 'oper': 'up', 'description': '[fi:ecn:10-20:rdma_f2_batch_001]', 'pvid': 10, 'link_type': 'trunk'}
```
Post-Recover:
```json
{'admin': 'up', 'oper': 'up', 'description': 'wj-lab-cpt-04', 'pvid': 10, 'link_type': 'trunk'}
```

## F-3 RDMA Load Imbalance - PASS

- Scenario ID: `rdma_load_imbalance`
- Inject: `True` | Error: `None`
- Recover: `True` | Error: `None`
- WAL Active Faults After Case: `0`

Baseline:
```json
{'admin': 'up', 'oper': 'up', 'description': 'wj-lab-cpt-04', 'pvid': 10, 'link_type': 'trunk'}
```
Post-Inject:
```json
{'admin': 'down', 'oper': 'down', 'description': 'wj-lab-cpt-04', 'pvid': 10, 'link_type': 'trunk'}
```
Post-Recover:
```json
{'admin': 'up', 'oper': 'down', 'description': 'wj-lab-cpt-04', 'pvid': 10, 'link_type': 'trunk'}
```

## F-4 RDMA Link Flap - PASS

- Scenario ID: `rdma_link_flap`
- Inject: `True` | Error: `None`
- Recover: `True` | Error: `None`
- WAL Active Faults After Case: `0`

Baseline:
```json
{'admin': 'up', 'oper': 'down', 'description': 'wj-lab-cpt-04', 'pvid': 10, 'link_type': 'trunk'}
```
Post-Inject:
```json
{'admin': 'up', 'oper': 'down', 'description': 'wj-lab-cpt-04', 'pvid': 10, 'link_type': 'trunk'}
```
Post-Recover:
```json
{'admin': 'up', 'oper': 'down', 'description': 'wj-lab-cpt-04', 'pvid': 10, 'link_type': 'trunk'}
```

## F-6 RDMA QoS Downgrade - PASS

- Scenario ID: `rdma_qos_downgrade`
- Inject: `True` | Error: `None`
- Recover: `True` | Error: `None`
- WAL Active Faults After Case: `0`

Baseline:
```json
{'admin': 'up', 'oper': 'down', 'description': 'wj-lab-cpt-04', 'pvid': 10, 'link_type': 'trunk'}
```
Post-Inject:
```json
{'admin': 'up', 'oper': 'down', 'description': '[fi:qos:dscp26->tc0:rdma_f6_batch_001]', 'pvid': 10, 'link_type': 'trunk'}
```
Post-Recover:
```json
{'admin': 'up', 'oper': 'down', 'description': 'wj-lab-cpt-04', 'pvid': 10, 'link_type': 'trunk'}
```

## F-5 RoCE MTU Mismatch - PASS

- Scenario ID: `roce_mtu_mismatch`
- Inject: `True` | Error: `None`
- Recover: `True` | Error: `None`
- WAL Active Faults After Case: `0`

Baseline:
```json
{'mtu': '9000'}
```
Post-Inject:
```json
{'mtu': '4096'}
```
Post-Recover:
```json
{'mtu': '9000'}
```
