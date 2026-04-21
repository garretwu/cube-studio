# Diagnosis Modified B2 Report Rail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild only the `????????` page into a two-pane B2 workspace with a left process timeline and a right current diagnosis-remediation report rail, while keeping the original `????` page untouched.

**Architecture:** Keep the original diagnosis page and model unchanged. Split the modified page into a dedicated timeline pane plus a report rail driven entirely by existing backend/frontend contract fields. Move heavy report semantics out of the left timeline and into the right rail, but continue using the left side for evidence, reasoning, execution, and event history. The report rail must stay compact and authoritative, not become a second event feed.

**Tech Stack:** React, TypeScript, existing local UI primitives (`AppIcon`, `StatusChip`, `SurfaceCard`-style system), Zustand-backed diagnosis store, existing diagnosis session/event types from `src/api/types.ts`.

---

## Constraints

- Do **not** modify the original page implementation in `src/pages/Diagnosis.tsx` or `src/pages/diagnosisModel.ts`.
- All new UI/data in the modified page must be derived from existing fields already present in:
  - `src/api/types.ts`
  - `API_INTERFACE_SPEC.md`
  - current session / event / audit data already consumed by the frontend
- Do **not** invent new backend fields or implicit contract assumptions.
- Keep the right rail compact. Avoid information overload and avoid rendering full evidence, propagation chain, raw tool output, or a second timeline in the rail.
- Treat `design.md` as the visual source of truth.
- Use `D:\work\toolcall\Toolcall` only as a structural reference for compact report cards and approval/report composition, not as a token/theme source.

## Visual Direction

### Hard visual requirements from this repo
- Light, neutral, cool operational console style from `design.md`
- Use typography, spacing, and borders for hierarchy before color
- Avoid "card for everything" sprawl; the right rail should feel like one report surface with internal sections
- Minimal shadow, thin borders, restrained accents, dense but readable spacing
- Strong separation between page background, work stage, left pane, and right report rail

### Secondary composition reference from `D:\work\toolcall\Toolcall`
- Compact uppercase/mono-like section labels are acceptable where helpful
- Report cards should feel precise and dense, with clearly bounded sections
- Approval controls should feel embedded in the report, not like a floating modal-first interaction
- Do not copy Tailwind/shadcn theme tokens or dark-mode behavior; keep this repo's existing CSS system and `design.md` surface language

## Existing Contract Fields Allowed In The Modified Report Rail

### Session-level
- `DiagnosisSession.session_id`
- `DiagnosisSession.alert`
- `DiagnosisSession.status`
- `DiagnosisSession.duration_seconds`
- `DiagnosisSession.outcome`
- `DiagnosisSession.re_diagnosis_round`
- `DiagnosisSession.diagnosis_result`
- `DiagnosisSession.trace`

### Diagnosis result
- `DiagnosisResult.root_cause`
- `DiagnosisResult.root_cause_layer`
- `DiagnosisResult.root_cause_entities`
- `DiagnosisResult.confidence`
- `DiagnosisResult.hypotheses`
- `DiagnosisResult.impact_summary`
- `DiagnosisResult.affected_services`
- `DiagnosisResult.triage_priority`
- `DiagnosisResult.diagnosis_certainty`
- `DiagnosisResult.propagation_chain` (allowed as source only; not recommended for direct rail rendering)
- `DiagnosisResult.ranked_candidates`
- `DiagnosisResult.recommended_fix`

### Recommended fix / plan
- `RemediationPlan.plan_id`
- `RemediationPlan.root_cause`
- `RemediationPlan.description`
- `RemediationPlan.steps`
- `RemediationPlan.canary`
- `RemediationPlan.estimated_impact`
- `RemediationPlan.confidence`
- `RemediationPlan.priority`
- `RemediationPlan.safety_level`

### Timeline / event / audit sources already in the app
- `SessionEvent`
- grouped run timeline items already derived in the diagnosis page logic
- `DiagnosisLocalAuditRecord`
- system event kinds already handled by the diagnosis flow (`approval_result`, `canary_progress`, `execution_progress`, `metric_feedback`, `alert_recovery`, `session_closed`)

## Right Rail Scope

### Keep
- Current conclusion
- Candidate changes (top 3 max)
- Current lifecycle stage
- Current execution summary
- Current feedback / result summary
- Next action / approval action area

### Exclude
- Full propagation chain panel
- Full evidence-for / evidence-against lists
- Full remediation step list
- Raw tool outputs
- Long reasoning copy
- Chronological change log
- Duplicate badges repeated in every section

## File Map

### Modify
- `src/pages/DiagnosisModifiedClone.tsx`
  - Convert the page into a true B2 two-pane layout
  - Stop rendering the heavy report card inside the left timeline
  - Render a dedicated right-side report rail
  - Move approval action placement into the right rail experience
- `src/pages/diagnosisModifiedModel.ts`
  - Make this the actual model source for the modified page
  - Add compact rail-specific derivation helpers using existing contract fields only
  - Add status-to-stage mapping and event compression helpers for the rail

### Create
- `src/pages/diagnosisModifiedReportModel.ts`
  - Dedicated compact view-model builders for the right report rail
  - Keep all rail shaping logic isolated from timeline shaping logic
- `src/pages/DiagnosisModifiedReportRail.tsx`
  - Right rail container and section composition
- `src/pages/DiagnosisModifiedReportSections.tsx`
  - Small focused presentational sections for the rail
- `src/pages/DiagnosisModifiedClone.test.tsx`
  - Tests for the new split layout and rail rendering behavior
- `src/pages/diagnosisModifiedReportModel.test.ts`
  - Tests for compact derivation logic and field minimization

### Optional create if file size grows too far
- `src/pages/DiagnosisModifiedTimelinePane.tsx`
  - Extract the left timeline pane rendering out of `DiagnosisModifiedClone.tsx`

---

### Task 1: Decouple The Modified Page From The Original Diagnosis Model

**Files:**
- Modify: `src/pages/DiagnosisModifiedClone.tsx`
- Modify: `src/pages/diagnosisModifiedModel.ts`
- Test: `src/pages/DiagnosisModifiedClone.test.tsx`

- [ ] Confirm `DiagnosisModifiedClone.tsx` no longer imports runtime view builders from `./diagnosisModel`
- [ ] Switch the modified page to use only `diagnosisModifiedModel.ts` builders for timeline, summary, candidates, hypotheses, and plan data
- [ ] Preserve the original route behavior and data sources so only presentation/model isolation changes in this task
- [ ] Add or adjust a test proving the modified page route can render independently of the original diagnosis model branch
- [ ] Run: `npm test -- src/pages/DiagnosisModifiedClone.test.tsx`
- [ ] Commit with scope-limited message such as `refactor(diagnosis-modified): isolate modified page model`

### Task 2: Introduce A Compact Report Rail View Model With Existing Fields Only

**Files:**
- Create: `src/pages/diagnosisModifiedReportModel.ts`
- Modify: `src/pages/diagnosisModifiedModel.ts`
- Test: `src/pages/diagnosisModifiedReportModel.test.ts`

- [ ] Add a compact rail view builder, for example `buildDiagnosisModifiedReportView(...)`
- [ ] Shape only these view groups:
  - `overview`
  - `conclusion`
  - `candidateChanges`
  - `stage`
  - `execution`
  - `feedback`
  - `nextAction`
- [ ] Ensure every output field is derived from already existing session/result/plan/event/audit fields
- [ ] Cap `candidateChanges` at 3 items maximum
- [ ] Exclude propagation chain, full evidence lists, and full remediation steps from the rail builder output
- [ ] Add tests proving:
  - ranked candidates are preferred when present
  - hypotheses are used as fallback
  - no more than 3 candidates are shown
  - execution summary compresses existing run/event data rather than listing raw events
  - feedback is derived from existing metric/recovery/close events only
- [ ] Run: `npm test -- src/pages/diagnosisModifiedReportModel.test.ts`
- [ ] Commit with message such as `feat(diagnosis-modified): add compact report rail model`

### Task 3: Define Minimal Stage Mapping And Keep It Contract-Based

**Files:**
- Modify: `src/pages/diagnosisModifiedReportModel.ts`
- Test: `src/pages/diagnosisModifiedReportModel.test.ts`

- [ ] Add a single frontend-only display mapping from `session.status` to a compact lifecycle label
- [ ] Use only existing statuses already present in the app and contract handling
- [ ] Support these display buckets only:
  - `????`
  - `????`
  - `????`
  - `????`
  - `????`
- [ ] Verify the mapping covers statuses already used in the repo such as:
  - `diagnosing`
  - `approval_required`
  - `approved`
  - `remediating`
  - `validating`
  - `resolved`
  - `closed`
  - `rejected`
- [ ] Add tests for the mapping function and helper copy
- [ ] Run: `npm test -- src/pages/diagnosisModifiedReportModel.test.ts`
- [ ] Commit with message such as `feat(diagnosis-modified): map session status to compact lifecycle display`

### Task 4: Build The Right Report Rail Sections

**Files:**
- Create: `src/pages/DiagnosisModifiedReportSections.tsx`
- Create: `src/pages/DiagnosisModifiedReportRail.tsx`
- Test: `src/pages/DiagnosisModifiedClone.test.tsx`

- [ ] Implement compact presentational sections only for:
  - overview
  - current conclusion
  - candidate changes
  - current stage
  - execution summary
  - feedback and next action
- [ ] Keep section density compact and calm: short labels, short values, short helper copy
- [ ] Use the repo's existing badge/card style language from `design.md`
- [ ] Use `toolcall` as a composition reference only for compact report grouping and restrained mono-like labels where helpful
- [ ] Do not create a colorful dashboard or a many-cards mosaic; keep it as a single report rail with internal sections
- [ ] Add tests proving the right rail renders key sections when the corresponding data exists
- [ ] Run: `npm test -- src/pages/DiagnosisModifiedClone.test.tsx`
- [ ] Commit with message such as `feat(diagnosis-modified): add report rail sections`

### Task 5: Convert The Modified Page Layout To B2

**Files:**
- Modify: `src/pages/DiagnosisModifiedClone.tsx`
- Optional Create: `src/pages/DiagnosisModifiedTimelinePane.tsx`
- Test: `src/pages/DiagnosisModifiedClone.test.tsx`

- [ ] Replace the current single-feed-first shell with a B2 layout:
  - left main timeline pane
  - right sticky report rail
- [ ] Keep the left side as the primary reading column
- [ ] Keep the right rail visually stable and narrower than the left pane
- [ ] Ensure the mobile layout collapses to report-first then timeline, not a broken two-column squeeze
- [ ] Add tests for:
  - report rail presence in modified page only
  - original timeline items still render on the left
  - report rail survives across live and demo states
- [ ] Run: `npm test -- src/pages/DiagnosisModifiedClone.test.tsx`
- [ ] Commit with message such as `feat(diagnosis-modified): switch modified workspace to split layout`

### Task 6: Remove The Heavy Report Card From The Left Timeline In The Modified Page

**Files:**
- Modify: `src/pages/DiagnosisModifiedClone.tsx`
- Modify: `src/pages/diagnosisModifiedModel.ts`
- Test: `src/pages/DiagnosisModifiedClone.test.tsx`

- [ ] Stop rendering the large `report` card body inside the left timeline for the modified page
- [ ] Preserve report data generation as source input for the right rail
- [ ] If needed, replace the old heavy report card with a lightweight timeline marker such as a report-updated row, but only if it uses existing summary/plan/session data and remains visually small
- [ ] Add tests proving the heavy timeline report card is absent from the modified page while the rail still shows current conclusion data
- [ ] Run: `npm test -- src/pages/DiagnosisModifiedClone.test.tsx`
- [ ] Commit with message such as `refactor(diagnosis-modified): move report semantics out of timeline`

### Task 7: Embed Approval Actions Into The Report Rail

**Files:**
- Modify: `src/pages/DiagnosisModifiedClone.tsx`
- Modify: `src/pages/DiagnosisModifiedReportRail.tsx`
- Modify: `src/pages/DiagnosisModifiedReportSections.tsx`
- Test: `src/pages/DiagnosisModifiedClone.test.tsx`

- [ ] Keep existing approval behavior and data sources, but move the primary approval interaction into the rail's next-action area for the modified page
- [ ] Only show approval action controls when `session.status` indicates approval is required and a plan exists
- [ ] Keep the original page behavior unchanged
- [ ] Reduce or remove reliance on the floating approval-first overlay in the modified page path; if the overlay remains temporarily, make the rail the primary surface and the overlay secondary
- [ ] Add tests for the approval-required state showing actions inside the rail
- [ ] Run: `npm test -- src/pages/DiagnosisModifiedClone.test.tsx`
- [ ] Commit with message such as `feat(diagnosis-modified): move approval actions into report rail`

### Task 8: Compress Execution Summary And Feedback Carefully

**Files:**
- Modify: `src/pages/diagnosisModifiedReportModel.ts`
- Modify: `src/pages/DiagnosisModifiedReportSections.tsx`
- Test: `src/pages/diagnosisModifiedReportModel.test.ts`

- [ ] Build execution summary from existing plan/run/system/audit data without exposing raw event streams
- [ ] Build feedback from existing metric/recovery/session-close signals only
- [ ] Limit execution summary to a few lines:
  - current action
  - canary/full/observation state when available
  - current blocking condition when available
- [ ] Limit feedback to at most 3 concise conclusions
- [ ] Add tests that guard against overpopulation of the rail sections
- [ ] Run: `npm test -- src/pages/diagnosisModifiedReportModel.test.ts`
- [ ] Commit with message such as `feat(diagnosis-modified): compress execution and feedback summaries`

### Task 9: Visual Audit Against `design.md` And `toolcall` Reference

**Files:**
- Modify: `src/pages/DiagnosisModifiedClone.tsx`
- Modify: `src/pages/DiagnosisModifiedReportRail.tsx`
- Modify: `src/pages/DiagnosisModifiedReportSections.tsx`
- Reference only: `design.md`
- Reference only: `D:\work\toolcall\Toolcall\src\app\components\RCACard.tsx`
- Reference only: `D:\work\toolcall\Toolcall\src\app\components\ApprovalCard.tsx`

- [ ] Audit the modified page against `design.md` and fix violations:
  - too many card boundaries
  - excessive shadows
  - decorative saturation
  - weak structural hierarchy
  - unclear background/stage/panel separation
- [ ] Audit composition against `toolcall` reference and borrow only:
  - compact report grouping
  - restrained section labeling
  - embedded approval/report interaction pattern
- [ ] Explicitly avoid copying:
  - theme tokens
  - dark-mode assumptions
  - Tailwind/shadcn component structure
- [ ] Capture before/after screenshots if useful for review
- [ ] Commit with message such as `style(diagnosis-modified): align rail composition with console design requirements`

### Task 10: End-To-End Verification For The Modified Page Only

**Files:**
- Test: `src/pages/DiagnosisModifiedClone.test.tsx`
- Test: `src/pages/diagnosisModifiedReportModel.test.ts`

- [ ] Run targeted tests for the modified page and model:
  - `npm test -- src/pages/diagnosisModifiedReportModel.test.ts`
  - `npm test -- src/pages/DiagnosisModifiedClone.test.tsx`
- [ ] If there are existing route-level app tests touching the modified route, run them too:
  - `npm test -- src/App.test.tsx`
- [ ] Manually verify these comparison points:
  - original `????` page remains unchanged
  - modified page shows B2 split layout
  - left side remains event/process oriented
  - right side remains compact and authoritative
  - approval-required state is readable without needing a giant floating report card
- [ ] Commit final integration changes with a message such as `feat(diagnosis-modified): add B2 report rail workflow`

---

## Review Checklist

### Spec coverage
- Modified page only: covered
- No new backend fields: covered
- Right rail remains compact: covered by rail scope and compression tasks
- Design alignment with `design.md`: covered
- Toolcall style used as structural reference only: covered

### Placeholder scan
- No `TODO` or `TBD` placeholders remain in this plan
- All tasks identify exact target files
- Verification commands are provided

### Type consistency
- Report rail data must derive from existing `DiagnosisSession`, `DiagnosisResult`, `RemediationPlan`, `SessionEvent`, and `DiagnosisLocalAuditRecord`
- No new API fields or fake contract types should be introduced during implementation

---

Plan complete and saved to `docs/superpowers/plans/2026-04-16-diagnosis-modified-b2-report-rail.md`.
