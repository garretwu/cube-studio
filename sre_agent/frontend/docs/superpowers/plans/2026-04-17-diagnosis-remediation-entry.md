# Diagnosis Remediation Entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a stable remediation entry to the diagnosis page and contextual remediation jump links in the feed, with navigation into `/remediation?sessionId=<id>` that auto-opens the selected remediation drawer.

**Architecture:** Keep remediation as a separate workspace. The diagnosis page will derive a session-level remediation link from the current session status and reuse the same route in remediation-related feed cards. The remediation page will keep using query-string-driven preselection, with tests tightened around auto-open and missing-session fallback.

**Tech Stack:** React, React Router, Zustand, Vitest, Testing Library, MSW

---

### Task 1: Lock navigation behavior with failing tests

**Files:**
- Modify: `src/pages/Diagnosis.test.tsx`
- Modify: `src/pages/Remediation.test.tsx`

- [ ] **Step 1: Write a failing diagnosis-page test for the header CTA**

Add a live-session test that renders the diagnosis route with an approval-required session, clicks the new remediation CTA, and expects the router to land on `/remediation?sessionId=sess-live-approval`.

- [ ] **Step 2: Run the diagnosis-page test to verify it fails**

Run: `npm test -- src/pages/Diagnosis.test.tsx --runInBand`
Expected: FAIL because the remediation CTA is not rendered yet.

- [ ] **Step 3: Write failing diagnosis-page tests for feed-level remediation links**

Add one live-flow test for a remediation-related system/run item and one demo-flow test for the demo execution card. Both should expect `打开修复概览` to appear after the existing item content and navigate to `/remediation?sessionId=...` when clicked.

- [ ] **Step 4: Re-run the diagnosis-page test file and confirm the new assertions fail for the expected reason**

Run: `npm test -- src/pages/Diagnosis.test.tsx --runInBand`
Expected: FAIL with missing link/button assertions.

- [ ] **Step 5: Write a failing remediation-page test for absent query-string targets**

Add a test that loads `/remediation?sessionId=sess-missing` and asserts the remediation drawer does not open while the page still renders the remediation record table.

- [ ] **Step 6: Run the remediation-page tests to verify the new test fails or exposes the current behavior gap**

Run: `npm test -- src/pages/Remediation.test.tsx --runInBand`
Expected: FAIL if the page auto-selects incorrectly, or PASS if behavior is already correct and only the diagnosis-page feature remains red.

### Task 2: Implement the diagnosis-page remediation entry points

**Files:**
- Modify: `src/pages/Diagnosis.tsx`
- Modify: `src/theme/components.css`

- [ ] **Step 1: Add route helpers and status-to-label mapping in the diagnosis page**

Create a small helper that builds `/remediation?sessionId=<id>` and a helper that maps diagnosis/remediation statuses to `审批修复`, `查看执行`, `查看修复记录`, or `查看修复概览`.

- [ ] **Step 2: Render the stable remediation CTA in the diagnosis status bar**

Use the active live session id and status to render a right-side navigation action in the diagnosis status bar. Keep it absent when no session id is available.

- [ ] **Step 3: Add contextual remediation links to remediation-related feed items**

Append a lightweight `打开修复概览` action to remediation-related system cards, approval-result cards, live run cards, and demo execution cards. Use the live session id when present and the demo summary session label for demo mode.

- [ ] **Step 4: Add focused CSS for the new header CTA and feed links**

Style the status-bar action so it reads as a navigation affordance and add lightweight footer/link styling for the feed cards without changing the existing card hierarchy.

- [ ] **Step 5: Run the diagnosis-page tests and make them pass**

Run: `npm test -- src/pages/Diagnosis.test.tsx --runInBand`
Expected: PASS for the new navigation and feed-link coverage.

### Task 3: Tighten remediation-page deep-link behavior and verify end to end

**Files:**
- Modify: `src/pages/Remediation.tsx`
- Modify: `src/pages/Remediation.test.tsx`

- [ ] **Step 1: Ensure query-string preselection keeps the selected drawer closed when the target session is absent**

Keep the existing preferred-session initialization, but confirm the selection logic only opens the drawer when the requested session exists in the loaded records.

- [ ] **Step 2: Re-run remediation-page tests and make them pass**

Run: `npm test -- src/pages/Remediation.test.tsx --runInBand`
Expected: PASS, including preselect and missing-session fallback.

- [ ] **Step 3: Run the focused diagnosis and remediation suites together**

Run: `npm test -- src/pages/Diagnosis.test.tsx src/pages/Remediation.test.tsx --runInBand`
Expected: PASS with zero failing tests in the touched areas.

- [ ] **Step 4: Review changed files and ensure the feature stays scoped**

Inspect the diff for:
- `src/pages/Diagnosis.tsx`
- `src/pages/Diagnosis.test.tsx`
- `src/pages/Remediation.tsx`
- `src/pages/Remediation.test.tsx`
- `src/theme/components.css`

Expected: only the diagnosis-to-remediation entry work is included.
