# Diagnosis To Remediation Entry Design

## Goal

On the diagnosis page, provide a stable remediation entry associated with the current session so the user can jump into the dedicated remediation workspace for that same session.

The entry should:

- live in the diagnosis session's right-side/status summary area rather than inside the main feed
- navigate to `/remediation?sessionId=<sessionId>` as a normal page transition
- auto-open the matching remediation drawer on the remediation page
- preserve the diagnosis page as a separate page in navigation history

In addition, the diagnosis feed should include lightweight remediation-related jump links after the existing demo-flow remediation events so the user can also enter the remediation workspace from the timeline context.

## Why

The diagnosis page is organized around a chronological feed. Remediation overview is not a single feed item; it is a dedicated workspace with plan details, execution progress, approval state, and timeline. A stable entry should therefore live with the session summary/status area, while the feed can expose contextual links at key remediation milestones.

This keeps the information architecture clear:

- diagnosis page: diagnosis context plus remediation summary and entry
- remediation page: remediation workspace and full execution detail

## Scope

In scope:

- add a stable remediation CTA in the diagnosis session header/status area
- derive CTA label from the current session remediation status
- navigate to `/remediation?sessionId=<id>`
- ensure remediation page preselects and auto-opens the target record drawer
- add contextual remediation jump links to remediation-related timeline items in the diagnosis demo/live feed
- add tests for the new navigation and deep-link behavior

Out of scope:

- redesign the remediation page layout
- embed remediation detail inside the diagnosis page
- move execution detail from remediation overview into session
- introduce a new cross-page state store for navigation

## Existing Constraints

- The diagnosis page already has a session/status bar at the top of the workspace.
- The diagnosis page main body is a feed and currently carries approval and execution-related items.
- The remediation page already supports `sessionId` from query string and opens a right-side drawer for a selected record.
- Remediation data is assembled from diagnosis session plus session events.

## Options Considered

### Option A: Stable header entry plus contextual feed links

Add a primary remediation link in the diagnosis session status area. Also add lightweight "open remediation overview" links on remediation-related timeline items.

Pros:

- stable entry for the whole session
- contextual entry near approval/execution events
- aligns with current page roles

Cons:

- slightly more implementation than a single entry

Recommendation: yes

### Option B: Feed-only links

Only show remediation links inside approval/execution/result cards in the feed.

Pros:

- minimal visual addition to the header

Cons:

- unstable entry location
- user must scroll to find it
- makes remediation workspace feel like a message instead of a session-level destination

Recommendation: no

### Option C: Header-only link

Only add the stable remediation entry in the session status area.

Pros:

- cleanest implementation
- stable location

Cons:

- weaker contextual affordance in the flow

Recommendation: acceptable fallback, but not preferred

## Selected Design

Use Option A.

### Diagnosis Page

Add a session-level remediation action in the top status/session summary area.

Behavior:

- only render when there is an active live session with a valid `session_id`
- route target is `/remediation?sessionId=<session_id>`
- use normal route navigation so browser back returns to the diagnosis page

Label mapping:

- `approval_required`, `awaiting_approval`, `proposed_fix_ready` -> `审批修复`
- `remediating`, `validating` -> `查看执行`
- `resolved`, `closed`, `failed`, `timeout`, `escalated`, `rejected` -> `查看修复记录`
- unknown but remediation-capable state -> `查看修复概览`

Placement:

- in the diagnosis workspace status/session area, visually grouped with session state instead of inside the feed
- styled as a clear navigation action, not as a destructive or submit button

### Diagnosis Feed

For remediation-related timeline/system items, append a lightweight jump link at the end of the card content:

- approval required
- approval result
- execution progress / canary progress / validation progress
- alert recovery
- session closed

Placement rule:

- append only after the existing content block for the current demo/live item
- do not insert before or in the middle of the current demo flow content
- the link text is `打开修复概览`

The feed link should navigate to the same route as the header CTA.

### Remediation Page

Keep the remediation page as the dedicated workspace.

Initialization behavior:

- read `sessionId` from query string on first load
- once records are loaded, if the target session exists in the result set, select it automatically
- opening the selected session must open the existing right-side drawer immediately
- this behavior should work whether the user enters directly by URL or from diagnosis-page navigation

Fallback behavior:

- if the target session does not exist, leave the page usable without crashing
- do not auto-open another unrelated record just because a query string was provided

## Data Flow

1. Diagnosis page reads current `session.session_id` and `session.status`.
2. Diagnosis page derives remediation CTA label from normalized status.
3. Clicking the CTA or feed link navigates to `/remediation?sessionId=<id>`.
4. Remediation page reads the query parameter during initialization.
5. After record loading finishes, remediation page selects the matching record.
6. Existing drawer logic opens the right-side detail panel for the selected record.

## Error Handling

- If there is no active session id, do not render the remediation link.
- If remediation records fail to load, the remediation page continues to show its existing error state.
- If the requested session id is not present in the loaded record list, keep the page loaded without auto-opening a wrong record.

## Testing

Add or update tests for:

- diagnosis page renders the header remediation entry for remediation-relevant session states
- diagnosis page header remediation entry navigates to `/remediation?sessionId=<id>`
- diagnosis feed remediation-related items render the contextual `打开修复概览` link after existing content
- clicking a feed link navigates to the remediation page with the correct query string
- remediation page auto-opens the correct drawer when `sessionId` is present in the URL
- remediation page stays stable when the requested `sessionId` is absent

## Implementation Notes

- Follow existing routing and query-string patterns already used in the remediation page.
- Reuse existing status normalization helpers where practical instead of inventing a separate state model.
- Keep the diagnosis page changes additive and local; do not restructure the whole feed rendering model for this feature.
