# Testing Strategy

## Stack
- Unit/Integration: Vitest (or Jest)
- E2E: Playwright
- Coverage: V8

## Coverage Targets
- Unit: 80% minimum
- Integration: All critical paths (auth, payments, data mutations)
- E2E: All main user journeys (signup, checkout, onboarding)

## Folder Rules
- Unit tests → tests/unit/ (mirror src/ structure exactly)
- Integration tests → tests/integration/
- E2E tests → tests/e2e/journeys/
- All mock data → tests/fixtures/ (never inline)
- All shared mocks → tests/mocks/

## Naming Conventions
- Unit: [module].test.ts
- Integration: [feature].integration.test.ts
- E2E: [journey].e2e.ts
- Test names: "should [expected behavior] when [condition]"

## What to Test
- Happy path (valid inputs)
- Edge cases (empty, null, zero, max values)
- Error cases (invalid input, failed external calls)
- Security-sensitive paths (auth, permissions) — always double-cover

## What NOT to Test
- Private methods or internal implementation
- Third-party library internals
- Trivial getters/setters with no logic

## Mocking Rules
- External services (Stripe, email, S3) → always mock in unit and integration
- Database → use test DB with real queries in integration, mock in unit
- Never mock the module you are testing

## Assertions
- Always use specific assertions (toBe, toEqual) over generic (toBeTruthy)
- Every test must have at least one assertion
- Test one concept per test — split if testing multiple behaviors

## Forbidden Patterns
- No Date.now() or Math.random() without mocking
- No hardcoded test data outside fixtures/
- No .only or .skip in committed code
- No testing implementation details
