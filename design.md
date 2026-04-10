# AIDC Console Design System

## 1. Visual Theme & Atmosphere

The AIDC Console should feel like a professional operational control plane: calm, precise, structured, and trustworthy. It is not a marketing site, not a consumer productivity app, and not a visually expressive AI showcase. The interface should communicate operational confidence through restraint — using disciplined typography, low-noise surfaces, subtle borders, and clear hierarchy instead of decorative effects.

The overall visual atmosphere should be light, neutral, and slightly cool in tone. Surfaces should feel crisp and engineered rather than soft or playful. Information should emerge through spacing, grouping, and typography first, with color reserved for meaning: selection, focus, status, and primary actions. The interface should support dense operational workflows without feeling cramped or chaotic.

Typography should carry more hierarchy than color. Surface separation should rely more on border precision, tonal distinction, and structural spacing than on shadows or filled blocks. The product should feel intentionally designed for infrastructure, topology, diagnosis, and operational workflows — not adapted from a landing page aesthetic.

**Key Characteristics:**
- Light-mode-native neutral console design
- Enterprise, restrained, stable, low-noise visual language
- Precise hierarchy driven by typography, spacing, and surface structure
- Single primary accent family used sparingly for interaction and focus
- Cool-neutral background and border system
- Subtle, thin borders instead of heavy outlines
- Minimal shadow usage; layering is communicated primarily through surface contrast
- Clear separation between background, stage, panel, and card
- Information-dense but readable
- Operational, task-oriented, trustworthy

---

## 2. Color Palette & Roles

### Background Surfaces
- **Page Background** (`#F5F6F8`): The outer application field. Calm, neutral, and quiet.
- **Work Stage** (`#FFFFFF`): The primary working surface where the main task happens.
- **Secondary Surface** (`#FAFBFC`): Secondary content background, quieter than the work stage.
- **Hover Surface** (`#F2F3F5`): Hover treatment for rows, controls, and interactive surfaces.

### Text & Content
- **Primary Text** (`#1F2329`): Main reading text, titles, and critical operational information.
- **Secondary Text** (`#4E5969`): Descriptions, secondary labels, and supportive interface copy.
- **Tertiary Text** (`#86909C`): Metadata, quiet labels, placeholders, and low-priority information.
- **Disabled / Weak Text** (`#B8BEC9`): Disabled states, unavailable content, and background metadata.

### Border & Divider
- **Primary Border** (`#E5E6EB`): Default structural border for cards, panels, inputs, and separators.
- **Secondary Border** (`#ECEEF2`): Lighter border for subtle separations and nested surfaces.
- **Strong Divider** (`#D9DDE4`): Higher-contrast separator for meaningful structural division.
- **Line Tint** (`#F0F2F5`): Extremely subtle line for low-priority separation where needed.

### Brand & Interactive Accent
- **Primary Brand** (`project-defined`): Use the existing project brand color as the only primary accent family.
- **Brand Hover** (`project-defined hover`): Slightly stronger interactive state of the primary brand.
- **Brand Soft Background** (`project-defined low-opacity tint`): Optional low-emphasis selected or focus-adjacent background when needed.

### Status Colors
- **Normal**: Neutral or green-gray
- **Info**: Muted blue-gray
- **Warning**: Muted amber
- **Critical**: Muted red

Status colors should be low-saturation and operational rather than decorative. They should appear in small, meaningful locations rather than filling large surfaces.

### Overlay
- **Overlay Primary** (`rgba(15, 23, 42, 0.32)`): Modal and dialog backdrop.
- **Overlay Strong** (`rgba(15, 23, 42, 0.44)`): Stronger isolation layer for critical overlays.

### Color Principles
- Default UI should remain predominantly neutral.
- Brand color is for action, selection, and focus — not decoration.
- Status color is for operational meaning only.
- Decorative color fields should be avoided.
- The page should not feel colorful by default.
- The interface should rely on tonal hierarchy more than chromatic hierarchy.

---

## 3. Typography Rules

### Font Family
- **Primary**: `Inter Variable`, with fallbacks: `Inter, SF Pro Display, -apple-system, BlinkMacSystemFont, system-ui, Segoe UI, Roboto, Helvetica Neue, Arial, sans-serif`
- **Monospace**: `Berkeley Mono`, with fallbacks: `ui-monospace, SFMono-Regular, SF Mono, Menlo, Consolas, monospace`
- **OpenType Features**: `"cv01", "ss03"` enabled globally when supported

If the codebase already uses Inter or Inter Variable, it should remain the canonical typeface. If not, the existing sane system stack may remain until a typography migration is intentionally planned.

### Hierarchy

| Role | Font | Size | Weight | Line Height | Letter Spacing | Notes |
|------|------|------|--------|-------------|----------------|-------|
| Page Title | Inter Variable | 28px (1.75rem) | 590 | 1.20 | -0.02em | Primary page titles |
| Section Title | Inter Variable | 18px (1.125rem) | 590 | 1.33 | -0.01em | Major section headers |
| Panel Title | Inter Variable | 16px (1.00rem) | 590 | 1.38 | normal | Panel and module titles |
| Card Title | Inter Variable | 14px (0.875rem) | 590 | 1.40 | normal | Object card headers |
| Body Large | Inter Variable | 15px (0.94rem) | 400 | 1.60 | normal | Slightly elevated body text |
| Body | Inter Variable | 14px (0.875rem) | 400 | 1.57 | normal | Default body text |
| Body Medium | Inter Variable | 14px (0.875rem) | 510 | 1.57 | normal | Key UI labels, emphasized body |
| Secondary Body | Inter Variable | 13px (0.81rem) | 400 | 1.54 | normal | Descriptions and secondary text |
| Secondary Medium | Inter Variable | 13px (0.81rem) | 510 | 1.54 | normal | Important supporting labels |
| Caption | Inter Variable | 12px (0.75rem) | 400 | 1.50 | normal | Metadata, timestamps |
| Caption Medium | Inter Variable | 12px (0.75rem) | 510 | 1.50 | normal | Strong metadata, badges, labels |
| Micro | Inter Variable | 11px (0.69rem) | 510 | 1.40 | normal | Tiny interface labels only when necessary |
| Mono Body | Berkeley Mono | 13px (0.81rem) | 400 | 1.50 | normal | Technical identifiers, code-like content |
| Mono Label | Berkeley Mono | 12px (0.75rem) | 400 | 1.40 | normal | Compact technical labels |

### Principles
- **510 is the default emphasis weight**: use it for UI emphasis, key labels, navigation, and selected states.
- **590 is reserved for titles and strong emphasis**: use it intentionally, not broadly.
- **400 is the reading weight**: body text should remain clear and calm.
- **No oversized display hierarchy**: internal product pages should not use marketing-style hero typography.
- **Typography should do more hierarchy work than color**.
- **Metadata should be quieter through size, contrast, and spacing — not weak hierarchy alone**.
- **Do not use weight 700 by default**.
- **Negative tracking should remain subtle**: use slightly tighter tracking for larger headings only, never dramatic display compression in normal product UI.

---

## 4. Component Stylings

### Buttons

**Primary Button**
- Background: `project-defined brand color`
- Text: `#FFFFFF`
- Padding: `8px 16px`
- Radius: `8px`
- Border: none
- Shadow: none by default
- Use: One primary action per section or workflow area

**Secondary Button**
- Background: `#FFFFFF`
- Text: `#1F2329`
- Border: `1px solid #E5E6EB`
- Padding: `8px 14px`
- Radius: `8px`
- Use: Secondary actions, parallel but lower-priority controls

**Tertiary / Text Button**
- Background: transparent
- Text: `#4E5969`
- Border: none
- Padding: minimal
- Radius: `8px`
- Use: Auxiliary actions, lightweight controls

**Subtle Toolbar Button**
- Background: `#FAFBFC`
- Text: `#4E5969`
- Border: `1px solid #ECEEF2`
- Padding: compact
- Radius: `8px`
- Use: Toolbar actions, filter-adjacent actions, quick controls

**Icon Button**
- Background: `#FFFFFF` or transparent depending on context
- Text: `#4E5969`
- Border: `1px solid #E5E6EB` when bounded
- Radius: `8px`
- Use: Compact action affordances, icon-only controls

### Cards & Containers
- Background: `#FFFFFF`
- Border: `1px solid #E5E6EB`
- Radius: `10px` or `12px` depending on scale
- Shadow: none by default
- Hover: subtle border or background shift only when interactive
- Use: object-level units, grouped result blocks, bounded operational entities

### Panels
- Background: `#FFFFFF` or `#FAFBFC` depending on hierarchy
- Border: `1px solid #E5E6EB` or tonal separation
- Radius: `12px`
- Shadow: none by default
- Internal Padding: consistent and generous enough for scanning
- Use: grouped functional sections within a stage

### Inputs & Forms

**Text Input**
- Background: `#FFFFFF`
- Text: `#1F2329`
- Border: `1px solid #E5E6EB`
- Padding: `8px 12px`
- Radius: `8px`

**Text Area**
- Background: `#FFFFFF`
- Text: `#1F2329`
- Border: `1px solid #E5E6EB`
- Padding: `12px 14px`
- Radius: `8px`

**Search Input**
- Background: `#FFFFFF`
- Text: `#1F2329`
- Border: `1px solid #E5E6EB`
- Padding: icon-aware, balanced horizontally
- Radius: `8px`

**Select / Filter Control**
- Background: `#FFFFFF`
- Text: `#1F2329`
- Border: `1px solid #E5E6EB`
- Radius: `8px`
- Use: structured control bars, scoped filtering, operational refinement

### Badges & Tags

**Status Badge**
- Background: restrained tint or neutral base
- Text: status-appropriate but muted
- Border: optional thin semantic border
- Radius: `6px`
- Font: `12px / 510`
- Use: operational state labeling

**Neutral Tag**
- Background: `#FAFBFC`
- Text: `#4E5969`
- Border: `1px solid #ECEEF2`
- Radius: `6px`
- Padding: compact and consistent
- Use: lightweight labeling, category indication, auxiliary tagging

**Dot Indicator**
- Small colored dot only
- Use: severity, status, inline operational state

### Navigation
- Background: neutral and quiet
- Links: `13px–14px`, weight `510`
- Active State: brand-accent or brand-adjacent selection treatment
- Grouping: explicit but subtle
- Use: frame the workspace, never dominate it

### Component Principles
- Components should feel engineered, not decorative.
- Surfaces should remain light and disciplined.
- Radius, border, and emphasis must remain consistent across the system.
- The UI should not drift into a “card for everything” pattern.
- Not every grouped region needs a fully elevated visual treatment.

---

## 5. Layout Principles

### Spacing System
- Base unit: 8px
- Preferred rhythm: 4px, 8px, 12px, 16px, 24px, 32px
- Micro-adjustments may be used for optical alignment, but the layout should still feel grid-governed

### Grid & Container
- Main content should use stable internal width constraints
- Wide areas should still preserve structure and scanning order
- Layouts should feel placed, not stretched arbitrarily
- Grouping should remain stronger than decoration

### Whitespace Philosophy
- Whitespace is a structural tool, not decoration
- Space should separate decisions, not just objects
- Dense interfaces should remain breathable through controlled spacing
- Clear whitespace is preferable to visual dividers when hierarchy is already obvious

### Section Separation
- Use spacing first
- Use border only when structure needs reinforcement
- Use surface contrast only when meaningful grouping is needed
- Avoid stacking multiple separation mechanisms at once unless necessary

### Layout Principles
- Structure before styling
- Group before card
- Stage before fragment
- Task flow before visual flourish
- Emphasis should follow semantic importance

---

## 6. Depth & Elevation

| Level | Treatment | Use |
|-------|-----------|-----|
| Background (Level 0) | `#F5F6F8`, no shadow | Outer application field |
| Stage (Level 1) | `#FFFFFF`, quiet, stable surface | Main work area |
| Panel (Level 2) | `#FFFFFF` or `#FAFBFC`, subtle border | Structured subsections |
| Card / Object (Level 3) | `#FFFFFF`, stronger local boundary if needed | Individual selectable or scannable entities |
| Floating UI (Level 4) | Light shadow + border + white surface | Popovers, menus, dialogs |
| Overlay (Level 5) | translucent overlay backdrop | Modal isolation |

**Elevation Philosophy**:  
Depth should be communicated primarily through surface contrast, border precision, and structural separation rather than heavy shadow. Most of the product should feel grounded, not floating. Shadow should be reserved for truly floating layers such as dialogs, popovers, or temporary overlays.

### Recommended Shadow Usage
- **Default**: no shadow
- **Hover Elevation**: very light shadow only when useful
- **Floating Element**: restrained layered shadow
- **Dialog**: slightly stronger but still controlled shadow stack

### Principles
- Do not use shadow as the primary grouping mechanism
- Do not create a floating-card aesthetic across the product
- Do not mix multiple unrelated elevation styles in the same context
- Use borders and spacing before shadows

---

## 7. Surface Hierarchy & Container Logic

### Surface Model
Every interface surface should clearly belong to one of these layers:

#### Background
The outer page field.  
Its role is to support content, not to behave like a content container.

#### Stage
The primary work surface where the main task happens.  
A screen or major area should generally feel like it has one primary stage.

#### Panel
A structured grouping area within the stage.  
Panels organize controls, summaries, details, or workflow subsections.

#### Card
A bounded object-level container used only when something needs to be:
- individually scanned
- selected
- compared
- treated as a discrete operational entity

### Surface Rules
- A screen should generally establish one clear primary work stage
- Background should remain quieter than the stage
- Panels should organize work inside the stage
- Cards should be reserved for object-level content, not every grouping
- Stage, panel, and card must not share identical visual meaning
- Large structural containers should be visually calmer than small actionable objects
- The more global the semantic level, the quieter it should appear
- The more specific the semantic unit, the more bounded it may become

### Consistency Rules
- Do not alternate randomly between “one fully enclosed content area” and “many scattered floating modules” at the same hierarchy level
- Do not place object cards directly on the page background when they semantically belong inside a stage or panel
- Do not make every subsection visually equivalent
- Do not let the background become a visual dumping ground for unrelated boxes

### When to Use a Card
Use a card only when the content is:
- an individual object
- a discrete result item
- a selectable unit
- an entity meant to be compared with siblings
- an operational object with its own state or actions

Do not use a card merely to create decoration or artificial fragmentation.

### Structural Goal
The interface should always make it easy to distinguish:
- what is the page background
- what is the main work area
- what is a grouped module
- what is an individual object

This rule exists to prevent the system from feeling inconsistent in how it separates background, content, and bounded containers.

---

## 8. Do's and Don'ts

### Do
- keep default UI neutral
- use typography, spacing, and border for hierarchy
- use one primary accent family only
- use color only when it carries meaning
- make selection visually distinct from abnormal state
- keep the system operational, calm, and trustworthy
- preserve structural clarity across different parts of the product
- maintain a consistent surface grammar
- improve clarity before adding visual personality

### Don't
- don't redesign the product into a marketing aesthetic
- don't use gradients as decoration
- don't make everything a pill
- don't overuse brand color
- don't turn every metric into a card by default
- don't let selected state and abnormal state conflict visually
- don't overuse shadows
- don't make all containers visually equivalent
- don't mix multiple competing container logics at the same hierarchy level
- don't treat the page background as a content grouping device

---

## 9. Responsive Behavior

### Breakpoints
| Name | Width | Key Changes |
|------|-------|-------------|
| Mobile Small | <600px | Single-column, compact spacing, reduced density |
| Mobile | 600–768px | Simplified grouping, stacked controls |
| Tablet | 768–1024px | Expanded panels, controlled multi-column layout |
| Desktop | 1024–1280px | Standard operational layout |
| Large Desktop | >1280px | Full-width workspace with internal constraints |

### Touch Targets
- Controls should retain comfortable touch size where applicable
- Buttons and inputs should remain height-consistent
- Compact controls must still preserve usability
- Dense does not mean hard to interact with

### Responsive Principles
- Hierarchy must remain stable as layouts collapse
- Control groups should stack cleanly
- Structural grouping must survive reduced width
- Visual density may compress, but semantic hierarchy must not collapse

---

## 10. Agent Prompt Guide

### Quick Color Reference
- Page Background: `#F5F6F8`
- Main Work Stage: `#FFFFFF`
- Secondary Surface: `#FAFBFC`
- Hover Surface: `#F2F3F5`
- Primary Text: `#1F2329`
- Secondary Text: `#4E5969`
- Tertiary Text: `#86909C`
- Weak Text: `#B8BEC9`
- Primary Border: `#E5E6EB`
- Secondary Border: `#ECEEF2`
- Strong Divider: `#D9DDE4`
- Primary Accent: existing project brand color
- Overlay: `rgba(15, 23, 42, 0.32)`

### Example Component Prompts
- "Design a structured operational panel on a light neutral console surface. Use a white background, 1px solid `#E5E6EB` border, 12px radius, no shadow. Panel title at 16px Inter Variable weight 590, body text at 14px weight 400, metadata at 12px weight 400 in `#86909C`."
- "Create a compact object card for infrastructure entities. White background, subtle border `#E5E6EB`, 10px radius, no default shadow, restrained spacing. Use 14px title weight 590, supporting values at 13px weight 400, and keep accent color only for selected state."
- "Build a control toolbar with lightweight filters. Use white inputs with 1px `#E5E6EB` border, 8px radius, 14px text, restrained focus ring, and ensure controls are visually lighter than the main content objects."
- "Create a neutral navigation area for an enterprise console. Use subtle grouping, 13–14px labels at weight 510, quiet neutral text, and apply the brand accent only to the active state."
- "Design a status badge system using muted semantic colors, compact 12px labels, restrained fills or borders, and avoid decorative saturation."

### Iteration Guide
1. Preserve the light neutral enterprise console foundation
2. Use typography, spacing, and borders before color or shadow
3. Keep one primary accent family only
4. Reserve strong emphasis for true priority, selection, or primary action
5. Distinguish background, stage, panel, and card clearly
6. Avoid turning every grouping into a card
7. Keep the interface operational and low-noise
8. Refine structure before refining style

---

## 11. AI Implementation Rules

When an AI coding agent modifies the UI, it should follow this order:

1. inspect the project styling system first
   - global.css
   - app.css
   - theme files
   - token files
   - Tailwind config
   - base UI components

2. prioritize global changes
   - design tokens
   - CSS variables
   - typography rules
   - spacing scale
   - radius scale
   - surface and border variants

3. then update shared components
   - Button
   - Card
   - Tag
   - Input
   - Select
   - Tabs
   - Sidebar
   - Panel
   - layout shells / stage wrappers

4. only then apply local refinements where needed

### Important Constraints
- do not change business logic
- do not remove real functionality
- do not heavily restructure DOM unless necessary
- do not introduce a new heavy UI framework
- do not create many redundant style files
- prefer updating the existing style system over adding parallel ones
- prefer solving hierarchy globally before patching individual interfaces

---

## 12. Deliverable Expectations for AI Agents

When completing a UI refactor, the AI agent should report:
1. which files were modified
2. which changes are globally effective
3. which changes are shared-component-level
4. which changes affect surface hierarchy or layout shells
5. which areas may still require manual visual tuning

---

## 13. Inspiration Boundaries

This system may draw inspiration from refined modern product design in:
- restraint
- hierarchy
- precision
- disciplined use of accent color
- typographic control
- subtle border language
- low-noise interface design

But this project must remain:
- lighter
- more operational
- more enterprise
- more task-oriented
- less brand-expressive
- less marketing-driven
- more structurally explicit

Use inspiration, not imitation.