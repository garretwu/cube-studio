# Candidate Root Cause Card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update the modified diagnosis report candidate cards to show colored certainty badges beside numeric confidence, rename and reposition the evidence toggle, and remove root-cause role badges.

**Architecture:** Keep this as a presentation-layer change inside the modified diagnosis report rail. Reuse existing candidate view-model fields where possible, add a small normalization helper for certainty labels/tones where needed, then update the candidate card layout and CSS so the meta row stays compact.

**Tech Stack:** React, TypeScript, Vitest, Testing Library, CSS

---

### Task 1: Lock the candidate card behavior with tests

**Files:**
- Modify: `src/pages/DiagnosisModified.test.tsx`

- [ ] **Step 1: Write the failing tests**

Add assertions in the modified diagnosis page tests that verify:

```tsx
expect(within(hypothesisCard).getByText("86%")).toBeInTheDocument();
expect(within(hypothesisCard).getByText("已确认")).toBeInTheDocument();
expect(within(hypothesisCard).getByRole("button", { name: "展开证据链" })).toBeInTheDocument();
expect(within(hypothesisCard).queryByText("Primary root cause")).not.toBeInTheDocument();
expect(within(hypothesisCard).queryByText("Candidate root cause")).not.toBeInTheDocument();
expect(within(hypothesisCard).queryByRole("button", { name: "Expand details" })).not.toBeInTheDocument();
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- src/pages/DiagnosisModified.test.tsx`
Expected: FAIL because the card still renders `Expand details`, still shows old role labels, or does not show the new certainty badge copy.

- [ ] **Step 3: Write minimal implementation support expectations**

Add a second rendered assertion for a non-confirmed card so the new badge mapping is covered:

```tsx
expect(within(reportRail).getByText("有可能")).toBeInTheDocument();
```

- [ ] **Step 4: Run test to verify it still fails for the right reason**

Run: `npm test -- src/pages/DiagnosisModified.test.tsx`
Expected: FAIL with missing updated UI strings rather than syntax or setup errors.

### Task 2: Implement the candidate card UI and certainty mapping

**Files:**
- Modify: `src/pages/DiagnosisModifiedReportSections.tsx`
- Modify: `src/theme/components.css`

- [ ] **Step 1: Add minimal certainty normalization helpers and card markup updates**

Implement a helper that maps candidate certainty labels into the module-specific labels and tones:

```ts
function getCandidateCertaintyMeta(label: string | undefined, tone: ReportTone) {
  const normalized = String(label ?? "").trim().toLowerCase();
  if (normalized.includes("confirmed") || label?.includes("已确认")) {
    return { label: "已确认", tone: "success" as const };
  }
  if (
    normalized.includes("probable") ||
    label?.includes("高概率") ||
    label?.includes("较大概率") ||
    label?.includes("有可能")
  ) {
    return { label: "有可能", tone: "warning" as const };
  }
  if (normalized.includes("ambiguous") || label?.includes("证据不足") || label?.includes("模糊")) {
    return { label: "模糊", tone: "neutral" as const };
  }
  return { label: "模糊", tone: tone === "success" ? "neutral" as const : tone };
}
```

Update the candidate card meta row so it renders:

```tsx
<div className="diagnosis-modified-report-rail__candidate-meta">
  <span>{item.confidenceLabel}</span>
  <ReportBadge label={certainty.label} tone={certainty.tone} />
  <button ...>{showDetails ? "收起证据链" : "展开证据链"}</button>
</div>
```

Remove the `Primary root cause` / `Candidate root cause` badge from the root-cause section candidate summaries.

- [ ] **Step 2: Update CSS for a compact meta row**

Adjust the candidate header and meta classes so the button sits inline with confidence and certainty, without introducing extra vertical space:

```css
.diagnosis-modified-report-rail__candidate-meta {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
}

.diagnosis-modified-report-rail__candidate-detail-toggle {
  margin-top: 0;
  width: auto;
  text-align: left;
  white-space: nowrap;
}
```

- [ ] **Step 3: Run the focused test to verify it passes**

Run: `npm test -- src/pages/DiagnosisModified.test.tsx`
Expected: PASS

### Task 3: Verify related report-model behavior and regression coverage

**Files:**
- Modify: `src/pages/diagnosisModifiedReportModel.test.ts`

- [ ] **Step 1: Add a focused regression test for candidate change display data**

Assert the report view still marks one primary candidate while preserving numeric confidence labels:

```ts
expect(view.candidateChanges[0]?.confidenceLabel).toBe("86%");
expect(view.candidateChanges[0]?.statusLabel).toBe("当前根因");
```

- [ ] **Step 2: Run the focused model test**

Run: `npm test -- src/pages/diagnosisModifiedReportModel.test.ts`
Expected: PASS

- [ ] **Step 3: Run both targeted suites together**

Run: `npm test -- src/pages/DiagnosisModified.test.tsx src/pages/diagnosisModifiedReportModel.test.ts`
Expected: PASS

- [ ] **Step 4: Review diff for unrelated edits**

Run: `git diff -- src/pages/DiagnosisModified.test.tsx src/pages/DiagnosisModifiedReportSections.tsx src/pages/diagnosisModifiedReportModel.test.ts src/theme/components.css`
Expected: only the candidate card copy/layout updates and test changes for this feature.
