# QinClaw UI Design System

## Source Of Truth
- Runtime theme source: `src/theme/tokens.ts`
- Runtime variable injection: `ensureThemeVariables()` in `src/theme/tokens.ts`
- Ant Design bridge: `src/theme/antdTheme.ts`
- Internal verification page: `src/pages/DesignTokens.tsx`
- Local icon registry: `src/components/ui/iconRegistry.tsx`

## Token Layers
- `foundation`
  - Raw values only: color scales, font families, font sizes, font weights, line heights, spacing, radius, shadow, layout, motion, icon geometry.
  - Canonical CSS vars use the `--foundation-*` namespace.
- `semantic`
  - Intent-level aliases: `text/*`, `surface/*`, `border/*`, `action/*`, `status/*`, `focus/*`, `icon/*`, and `typography/*`.
  - Canonical CSS vars use the `--semantic-*` namespace.
- `component`
  - Scoped tokens only for primitives currently used by the app:
    - `AppButton`
    - `AppInput`
    - `StatusChip`
    - `SurfaceCard`
    - `SidebarNav`
    - `Topbar`
    - `MetricTile`
    - `SectionHeader`
  - Canonical CSS vars use the `--component-*` namespace.
- `icon`
  - Shared icon size, stroke width, and color tokens.
  - Canonical CSS vars use the `--icon-*` namespace.

## Compatibility Strategy
- Existing legacy variables remain available as aliases:
  - `--color-brand-600`
  - `--space-4`
  - `--radius-lg`
  - `--font-display`
  - other previously shipped root vars
- New work should prefer canonical layered vars over legacy aliases.
- Layout scale values can still consume foundation tokens directly.

## Runtime Rules
- Tokens are serialized from TypeScript into a single `:root` style block at bootstrap.
- CSS files should consume `var(--...)`, not duplicate raw hex, radius, or font values.
- `DesignTokens` reads live runtime CSS variables rather than maintaining separate demo data.
- Ant Design must not hardcode theme values outside the shared token source.

## Background Layering
- `semantic.background.*` is reserved for the bottom-most page atmosphere only.
- `semantic.surface.*` is reserved for content-bearing surfaces such as cards, panels, sidebars, drawers, and graph canvases.
- Ambient gradients belong on the page background layer, while content surfaces should stay solid and visually quiet by default.

## Component Consumption Priority
1. Prefer `component` tokens for shared UI primitives.
2. Fall back to `semantic` tokens for intent-level styling.
3. Use `foundation` tokens only for raw layout scale or primitive geometry.
4. Keep legacy aliases only for compatibility, not for new authoring.

## Icon Registry
- `AppIcon` is the single public icon entry point.
- Public API:
  - `name: AppIconName`
  - `variant?: "outline" | "fill"` with `outline` as the default
  - existing `size` and `decorative` props stay supported
- The registry is local TSX and does not depend on Figma asset URLs at runtime.
- Legacy names stay mapped for current app usage:
  - `topology -> layers`
  - `alerts -> notification`
  - `diagnosis -> chart`
  - `remediation -> clipboardTasks`
  - `chat -> aiChat`
  - `knowledge -> document`
  - `memory -> timeCircle`
  - `skills -> algorithm`
  - `chevronDown -> down`
  - `chevronRight -> right`
  - `spark -> star`
  - `help -> helpSquare`
- The internal icon catalog on the design-system page is the fast verification surface for `name + variant`.

## Extension Rules
- Add new design tokens in `src/theme/tokens.ts`, not in CSS files or Ant Design config.
- Update primitives to consume canonical layered vars before introducing new one-off classes.
- Add new icons to `iconRegistry.tsx`, not to a `switch` statement and not through third-party icon packages.
- Keep business pages out of the icon catalog; it remains an internal design-system verification surface.
