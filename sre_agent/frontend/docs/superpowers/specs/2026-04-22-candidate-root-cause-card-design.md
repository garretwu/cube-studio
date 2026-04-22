# Candidate Root Cause Card Design

## Goal

Adjust the candidate root-cause module so operators can scan confidence and certainty together, access the evidence chain with less layout cost, and remove redundant root-cause role labels.

## Scope

This change applies to the candidate root-cause presentation in the modified diagnosis report rail and related candidate summary surfaces that currently show:

- numeric confidence only
- an `Expand details` action
- `Primary root cause` / `Candidate root cause` labels

## User-Facing Changes

### 1. Confidence + certainty shown together

Each candidate card should display:

- the numeric confidence value, such as `91%`
- a certainty badge with color, rendered beside the confidence value

The certainty badge should use these labels:

- `confirmed` -> `已确认`
- `probable` -> `有可能`
- `ambiguous` -> `模糊`

When the candidate already carries an existing localized certainty-like label from current view-model logic, the implementation should normalize it to the three target labels above for the candidate root-cause module instead of showing inconsistent synonyms.

### 2. Evidence expansion action becomes compact and semantic

Replace the `Expand details` / `Collapse details` copy with:

- collapsed: `展开证据链`
- expanded: `收起证据链`

This action should move into the confidence/certainty meta row so it sits near the confidence information and does not increase card header height significantly.

### 3. Remove role labels

Remove `Primary root cause` / `Candidate root cause` badges from candidate cards in this module.

Rank, title, confidence, certainty, summary, and evidence-chain content remain visible.

## Visual Rules

### Certainty badge colors

Use visually distinct tones:

- `已确认`: success tone
- `有可能`: warning or accent tone that is clearly distinct from success
- `模糊`: neutral tone

The chosen colors should align with the existing diagnosis report rail palette and badge system instead of introducing a new visual language.

### Density

The candidate header should stay compact:

- title remains the primary text line
- confidence, certainty badge, and evidence toggle should share a single compact meta row when space allows
- mobile layout may wrap this row, but should still avoid introducing a large top gap

## Implementation Notes

- Prefer reusing existing badge/tone primitives.
- Avoid changing underlying diagnosis semantics.
- This is a presentation-layer change only; no API contract changes are needed.
- Because the worktree is already dirty in the relevant files, implementation must preserve unrelated in-progress edits.

## Testing

Add or update tests to verify:

- candidate cards show confidence value and the translated certainty badge together
- the old `Expand details` text is removed
- the new `展开证据链` action appears
- the old `Primary root cause` / `Candidate root cause` labels are absent
- certainty badge colors/tones differ by certainty state at the view-model or rendered level that existing tests can reasonably assert
