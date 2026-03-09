# RDMA Real Injection Validation Runbook

> Project: `fault_injector`
> 
> Scope: Real-device validation for RDMA scenarios F-1~F-6
> 
> Last verified date: 2026-02-27

## 1. Purpose

This runbook defines a repeatable process to validate RDMA fault scenarios on real infrastructure with safety gates, baseline checks, post-injection verification, and rollback verification.

## 2. Target Environment

### 2.1 Switch

- Switch ID: `Switch200GE`
- Host: `10.11.8.52`
- Port: `830` (NETCONF)
- Username: `apitest`
- Password: `ApiTest@2026`
- Test interface: `200GE1/0/4`
- IfIndex: `4`
- Baseline description: `wj-lab-cpt-04`
- Baseline state: `admin=UP`, `pvid=10`, `link_type=trunk`

### 2.2 Host (for F-5)

- Host: `10.11.4.13`
- Username: `yuyonghao`
- Password: `Yuyonghao@123`
- RDMA interface: `roce200`
- Baseline MTU: `9000`

## 3. Safety Rules

1. Use only dedicated test interface `200GE1/0/4`.
2. Always collect baseline before injection.
3. Always verify post-injection state and post-recovery state.
4. Fail fast and recover immediately on any verification failure.
5. WAL active faults must return to `0` after each scenario.

## 4. Preconditions

1. NETCONF connectivity is healthy.
2. SSH connectivity to host `10.11.4.13` is healthy.
3. No production traffic depends on test interface.
4. `guard.py` policy is enabled.

## 5. Validation Procedure

### 5.1 Low-risk channel verification

Run switch operation integration test first:

```bash
python -m fault_injector.tests.integration.test_switch_operations --dry-run
python -m fault_injector.tests.integration.test_switch_operations --real
```

Expected:
- shutdown succeeds
- bringup succeeds
- final admin state returns to `up`

### 5.2 Scenario-level real validation sequence

Recommended order:

1. `F-4 rdma_link_flap`
2. `F-3 rdma_load_imbalance`
3. `F-1 pfc_deadlock`
4. `F-2 ecn_misconfiguration`
5. `F-6 rdma_qos_downgrade`
6. `F-5 roce_mtu_mismatch`

For each scenario, collect:

1. Baseline evidence
2. Inject result + post-injection state
3. Recover result + post-recovery state
4. WAL active fault count

## 6. Evidence Template

Use this record format per scenario:

```text
[SCENARIO] <name>
[BASELINE] <state summary>
[INJECT] success=<bool> error=<error_or_none>
[POST-INJECT] <state summary>
[RECOVER] success=<bool> error=<error_or_none>
[POST-RECOVER] <state summary>
[WAL] active_faults=<count>
```

## 7. Real Validation Results (2026-02-27)

### 7.1 Switch scenarios

- `F-4 rdma_link_flap`: PASS
- `F-3 rdma_load_imbalance`: PASS
- `F-1 pfc_deadlock`: PASS
- `F-2 ecn_misconfiguration`: PASS
- `F-6 rdma_qos_downgrade`: PASS

All recovered to baseline description `wj-lab-cpt-04`.

### 7.2 Host scenario

- `F-5 roce_mtu_mismatch`: PASS
- MTU evidence: `9000 -> 4096 -> 9000`

### 7.3 WAL

- Final active faults: `0`

## 8. Known Implementation Notes

1. This device does not safely support restoring empty interface description with current Ifmgr path.
2. Use non-empty baseline description (`wj-lab-cpt-04`) for marker-based switch scenario validation.
3. On Windows controller, asyncssh may fail reading local ssh config due to encoding; current code avoids this by passing `config=[]`.

## 9. Recovery Checklist

If any scenario fails:

1. Stop further scenario execution.
2. Execute scenario recover path immediately.
3. Verify interface admin/pvid/link_type/description returned to baseline.
4. Verify WAL active faults is `0`.
5. Record failure evidence for root-cause analysis.
