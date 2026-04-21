# DiagnosisModified Demo Post-Approval Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the diagnosis demo continue automatically after repair approval and surface session-linked remediation status updates through repair completion and session closure.

**Architecture:** Keep the page as a diagnosis page and reuse the existing report model. Extend the `DiagnosisModified` demo-only state machine so approval triggers a scheduled sequence of demo audit records and session status transitions, then let the existing left trace card and right report rail render those state changes.

**Tech Stack:** React, TypeScript, Vitest, Testing Library

---

### Task 1: Lock the expected demo behavior with tests

**Files:**
- Modify: `src/pages/DiagnosisModified.test.tsx`
- Test: `src/pages/DiagnosisModified.test.tsx`

- [ ] **Step 1: Write the failing test**

Add a demo interaction test that starts the scenario, clicks the inline approval button, then asserts:
- an approval result record appears
- session-linked feedback for metric recovery, alert recovery, and session closure appears
- the action-generated area and report rail reflect the progressed state

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- src/pages/DiagnosisModified.test.tsx --runInBand`
Expected: FAIL in the new demo post-approval assertions because the current handler only flips local approval text.

- [ ] **Step 3: Write minimal implementation**

Update `DiagnosisModified.tsx` to schedule a demo-only post-approval flow that mutates:
- `demoSession.status`
- `demoEvents`
- `demoLocalAuditRecords`
- optional demo trace messages needed to make the left-side timeline feel synchronized

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- src/pages/DiagnosisModified.test.tsx --runInBand`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/pages/DiagnosisModified.tsx src/pages/DiagnosisModified.test.tsx docs/superpowers/plans/2026-04-21-diagnosis-modified-demo-post-approval-sync.md
git commit -m "feat(frontend): continue diagnosis demo after repair approval"
```

### Task 2: Verify report-model compatibility stays intact

**Files:**
- Modify: `src/pages/diagnosisModifiedReportModel.test.ts`
- Test: `src/pages/diagnosisModifiedReportModel.test.ts`

- [ ] **Step 1: Write or extend a failing test if needed**

If the first task reveals a model mismatch, add a focused test that feeds `approval_result`, `metric_feedback`, `alert_recovery`, and `session_closed` records and verifies the compressed feedback list ordering and execution summary.

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- src/pages/diagnosisModifiedReportModel.test.ts --runInBand`
Expected: FAIL only if model changes are needed.

- [ ] **Step 3: Write minimal implementation**

Only change `src/pages/diagnosisModifiedReportModel.ts` if the new demo records expose a real gap. Prefer no model changes unless the test proves one is required.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- src/pages/diagnosisModifiedReportModel.test.ts --runInBand`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/pages/diagnosisModifiedReportModel.ts src/pages/diagnosisModifiedReportModel.test.ts
git commit -m "test(frontend): cover diagnosis modified remediation feedback sync"
```

### Task 3: Final verification

**Files:**
- Modify: `src/pages/DiagnosisModified.tsx`
- Modify: `src/pages/DiagnosisModified.test.tsx`

- [ ] **Step 1: Run the focused page tests**

Run: `npm test -- src/pages/DiagnosisModified.test.tsx --runInBand`
Expected: PASS

- [ ] **Step 2: Run the focused report-model tests**

Run: `npm test -- src/pages/diagnosisModifiedReportModel.test.ts --runInBand`
Expected: PASS

- [ ] **Step 3: Sanity-check for unintended regressions**

Run: `npm test -- src/pages/DiagnosisModified.test.tsx src/pages/diagnosisModifiedReportModel.test.ts --runInBand`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/pages/DiagnosisModified.tsx src/pages/DiagnosisModified.test.tsx src/pages/diagnosisModifiedReportModel.test.ts docs/superpowers/plans/2026-04-21-diagnosis-modified-demo-post-approval-sync.md
git commit -m "feat(frontend): sync diagnosis demo with remediation session states"
```
