export type ThemeTokenLayer = "foundation" | "semantic" | "component" | "icon";

export type ThemeTokenValue = string | number;

type ThemeTokenTree = {
  [key: string]: ThemeTokenTree | ThemeTokenValue;
};

export type ThemeVariables = Record<`--${string}`, string>;

export type ThemeVariableDefinition = {
  name: `--${string}`;
  value: string;
  layer: ThemeTokenLayer;
  path: string[];
  label: string;
  isAlias?: boolean;
};

const BRAND = {
  100: "#eeebff",
  200: "#dbd6ff",
  400: "#8579ff",
  500: "#6555ff",
  600: "#503dff",
  700: "#3f30d0",
} as const;

const NEUTRAL = {
  0: "#ffffff",
  25: "#fafbfc",
  50: "#f5f6f8",
  100: "#f2f3f5",
  200: "#e5e6eb",
  300: "#d9dde4",
  400: "#c9ced6",
  500: "#86909c",
  600: "#4e5969",
  700: "#3a4554",
  800: "#2a313a",
  950: "#1f2329",
} as const;

const STATUS = {
  success: {
    100: "#eef7f1",
    600: "#4f8f72",
    700: "#3b6f59",
  },
  warning: {
    100: "#fdf4e7",
    600: "#b88230",
    700: "#8d6420",
  },
  danger: {
    100: "#fbecec",
    600: "#c85656",
    700: "#9d3f3f",
  },
  info: {
    100: "#edf3f8",
    600: "#5a86a8",
    700: "#436783",
  },
} as const;

export const foundationTokens = {
  color: {
    brand: BRAND,
    neutral: NEUTRAL,
    success: STATUS.success,
    warning: STATUS.warning,
    danger: STATUS.danger,
    info: STATUS.info,
    accent: {
      sky: "#7fc7ff",
      avatar: "#7cc4ff",
    },
  },
  fontFamily: {
    sans: '"Inter", "Segoe UI", sans-serif',
    display: '"Inter Tight", "Inter", "Segoe UI", sans-serif',
    mono: 'ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace',
  },
  fontSize: {
    caption: "12px",
    body: "14px",
    bodyLg: "14px",
    sectionTitle: "18px",
    pageTitle: "28px",
    metric: "28px",
    mono: "12px",
  },
  fontWeight: {
    regular: "400",
    medium: "500",
    semibold: "600",
    bold: "700",
  },
  lineHeight: {
    caption: "16px",
    body: "22px",
    bodyLg: "22px",
    sectionTitle: "26px",
    pageTitle: "36px",
    metric: "30px",
    mono: "18px",
  },
  letterSpacing: {
    tight: "-0.02em",
    section: "-0.015em",
    label: "0.08em",
    normal: "0",
  },
  space: {
    1: "4px",
    2: "8px",
    3: "12px",
    4: "16px",
    5: "20px",
    6: "24px",
    7: "32px",
    8: "40px",
    9: "48px",
    10: "64px",
  },
  radius: {
    xs: "8px",
    sm: "10px",
    md: "12px",
    lg: "12px",
    xl: "12px",
    full: "999px",
  },
  shadow: {
    sm: "0 1px 2px rgba(31, 35, 41, 0.04)",
    md: "0 6px 18px rgba(31, 35, 41, 0.05)",
    lg: "0 12px 30px rgba(31, 35, 41, 0.08)",
    inset: "inset 0 1px 0 rgba(255, 255, 255, 0.4)",
  },
  layout: {
    sidebarWidth: "272px",
    contentMaxWidth: "1280px",
    metricTileMinHeight: "112px",
    controlHeightSm: "32px",
    controlHeightMd: "40px",
    controlHeightLg: "44px",
  },
  motion: {
    fast: "180ms ease",
    base: "240ms ease",
  },
  icon: {
    size: {
      sm: "16px",
      md: "18px",
      lg: "24px",
    },
    strokeWidth: {
      thin: "1.5",
      regular: "1.8",
      bold: "2",
    },
  },
} as const;

export const semanticTokens = {
  text: {
    primary: NEUTRAL[950],
    secondary: NEUTRAL[600],
    tertiary: NEUTRAL[500],
    inverse: NEUTRAL[0],
    accent: BRAND[600],
  },
  background: {
    pageBase: NEUTRAL[50],
    pageAlt: NEUTRAL[25],
    pageDepth: NEUTRAL[100],
    ambientBrand: "#f7f6ff",
    ambientInfo: "#f5f7fa",
  },
  surface: {
    stage: NEUTRAL[0],
    panel: NEUTRAL[0],
    panelMuted: NEUTRAL[25],
    panelSoft: NEUTRAL[100],
    panelStrong: NEUTRAL[25],
    panelBrand: "#f4f3ff",
    card: NEUTRAL[0],
    workplane: NEUTRAL[25],
    floating: "rgba(255, 255, 255, 0.98)",
    panelOverlay: "rgba(255, 255, 255, 0.96)",
    glass: "rgba(255, 255, 255, 0.96)",
  },
  border: {
    default: NEUTRAL[200],
    strong: NEUTRAL[300],
    subtle: "rgba(31, 35, 41, 0.08)",
    accent: "rgba(80, 61, 255, 0.18)",
    inverse: "rgba(255, 255, 255, 0.12)",
  },
  action: {
    primary: BRAND[600],
    primaryHover: BRAND[500],
    primaryActive: BRAND[700],
    subtle: "rgba(31, 35, 41, 0.04)",
    subtleHover: "rgba(31, 35, 41, 0.06)",
    selected: NEUTRAL[100],
    disabledSurface: NEUTRAL[200],
    disabledText: NEUTRAL[500],
  },
  status: {
    success: STATUS.success[600],
    successSoft: STATUS.success[100],
    successStrong: STATUS.success[700],
    warning: STATUS.warning[600],
    warningSoft: STATUS.warning[100],
    warningStrong: STATUS.warning[700],
    danger: STATUS.danger[600],
    dangerSoft: STATUS.danger[100],
    dangerStrong: STATUS.danger[700],
    info: STATUS.info[600],
    infoSoft: STATUS.info[100],
    infoStrong: STATUS.info[700],
  },
  focus: {
    outline: "rgba(80, 61, 255, 0.26)",
    ring: "rgba(80, 61, 255, 0.18)",
    ringSoft: "rgba(80, 61, 255, 0.1)",
  },
  icon: {
    default: NEUTRAL[950],
    muted: NEUTRAL[600],
    accent: BRAND[600],
    inverse: NEUTRAL[0],
    active: BRAND[700],
  },
  typography: {
    pageTitle: {
      family: foundationTokens.fontFamily.display,
      size: foundationTokens.fontSize.pageTitle,
      weight: foundationTokens.fontWeight.bold,
      lineHeight: foundationTokens.lineHeight.pageTitle,
      letterSpacing: foundationTokens.letterSpacing.tight,
    },
    sectionTitle: {
      family: foundationTokens.fontFamily.display,
      size: foundationTokens.fontSize.sectionTitle,
      weight: foundationTokens.fontWeight.semibold,
      lineHeight: foundationTokens.lineHeight.sectionTitle,
      letterSpacing: foundationTokens.letterSpacing.section,
    },
    body: {
      family: foundationTokens.fontFamily.sans,
      size: foundationTokens.fontSize.body,
      weight: foundationTokens.fontWeight.regular,
      lineHeight: foundationTokens.lineHeight.body,
      letterSpacing: foundationTokens.letterSpacing.normal,
    },
    caption: {
      family: foundationTokens.fontFamily.sans,
      size: foundationTokens.fontSize.caption,
      weight: foundationTokens.fontWeight.medium,
      lineHeight: foundationTokens.lineHeight.caption,
      letterSpacing: foundationTokens.letterSpacing.label,
    },
    mono: {
      family: foundationTokens.fontFamily.mono,
      size: foundationTokens.fontSize.mono,
      weight: foundationTokens.fontWeight.medium,
      lineHeight: foundationTokens.lineHeight.mono,
      letterSpacing: foundationTokens.letterSpacing.normal,
    },
  },
} as const;

export const componentTokens = {
  appButton: {
    radius: foundationTokens.radius.xs,
    gap: foundationTokens.space[2],
    height: {
      sm: foundationTokens.layout.controlHeightSm,
      md: foundationTokens.layout.controlHeightMd,
      lg: foundationTokens.layout.controlHeightLg,
    },
    paddingInline: {
      sm: "12px",
      md: "16px",
      lg: "18px",
    },
    fontSize: foundationTokens.fontSize.body,
    fontWeight: foundationTokens.fontWeight.semibold,
    primary: {
      text: semanticTokens.text.inverse,
      border: semanticTokens.action.primary,
      background: semanticTokens.action.primary,
      shadow: "0 1px 2px rgba(31, 35, 41, 0.12)",
    },
    secondary: {
      text: semanticTokens.text.primary,
      border: "transparent",
      background: semanticTokens.surface.panelMuted,
    },
    tertiary: {
      text: semanticTokens.text.secondary,
      border: "transparent",
      background: "transparent",
    },
    danger: {
      text: semanticTokens.status.dangerStrong,
      border: "rgba(200, 86, 86, 0.18)",
      background: "rgba(200, 86, 86, 0.08)",
    },
    disabledOpacity: "0.55",
  },
  appInput: {
    minHeight: foundationTokens.layout.controlHeightMd,
    radius: foundationTokens.radius.sm,
    background: semanticTokens.surface.panel,
    border: semanticTokens.border.default,
    shadow: "none",
    text: semanticTokens.text.primary,
    adornment: semanticTokens.text.secondary,
    placeholder: semanticTokens.text.tertiary,
    focusBorder: semanticTokens.action.primaryHover,
    focusRing: `0 0 0 3px ${semanticTokens.focus.ringSoft}`,
    textareaMinHeight: "120px",
  },
  statusChip: {
    minHeight: "24px",
    paddingInline: "8px",
    gap: "6px",
    radius: "6px",
    fontSize: foundationTokens.fontSize.caption,
    fontWeight: foundationTokens.fontWeight.medium,
    neutral: {
      text: semanticTokens.text.secondary,
      background: semanticTokens.surface.panelSoft,
    },
    accent: {
      text: semanticTokens.icon.accent,
      background: "rgba(80, 61, 255, 0.08)",
      border: "rgba(80, 61, 255, 0.12)",
    },
    success: {
      text: semanticTokens.status.successStrong,
      background: semanticTokens.status.successSoft,
    },
    warning: {
      text: semanticTokens.status.warningStrong,
      background: semanticTokens.status.warningSoft,
    },
    danger: {
      text: semanticTokens.status.dangerStrong,
      background: semanticTokens.status.dangerSoft,
    },
    info: {
      text: semanticTokens.status.infoStrong,
      background: semanticTokens.status.infoSoft,
    },
  },
  surfaceCard: {
    radius: foundationTokens.radius.xl,
    border: semanticTokens.border.default,
    background: semanticTokens.surface.panel,
    shadow: "none",
    stageBackground: semanticTokens.surface.stage,
    stageBorder: "transparent",
    panelBackground: semanticTokens.surface.panel,
    panelBorder: semanticTokens.border.default,
    cardBackground: semanticTokens.surface.card,
    cardBorder: semanticTokens.border.strong,
    heroGlow: "none",
    softBackground: semanticTokens.surface.panelMuted,
    headerGap: foundationTokens.space[4],
    sectionPadding: foundationTokens.space[5],
  },
  sidebarNav: {
    brandMarkBackground: semanticTokens.surface.panel,
    brandMarkShadow: "none",
    sectionLabelColor: semanticTokens.text.secondary,
    itemRadius: foundationTokens.radius.xs,
    itemText: semanticTokens.text.secondary,
    itemHoverText: semanticTokens.text.primary,
    itemHoverBackground: semanticTokens.action.subtle,
    itemActiveText: semanticTokens.text.primary,
    itemActiveBackground: semanticTokens.surface.panelSoft,
  },
  topbar: {
    compactHeight: foundationTokens.layout.controlHeightSm,
    compactPaddingInline: "12px",
    userBackground: semanticTokens.surface.panel,
    userBorder: semanticTokens.border.default,
    avatarBackground: "#e9edf4",
  },
  metricTile: {
    minHeight: foundationTokens.layout.metricTileMinHeight,
    radius: foundationTokens.radius.lg,
    border: semanticTokens.border.default,
    background: semanticTokens.surface.panel,
    shadow: "none",
  },
  sectionHeader: {
    eyebrowColor: semanticTokens.text.secondary,
    eyebrowLetterSpacing: foundationTokens.letterSpacing.label,
    titleFamily: semanticTokens.typography.pageTitle.family,
    titleColor: semanticTokens.text.primary,
    titleWeight: semanticTokens.typography.pageTitle.weight,
    titleLetterSpacing: semanticTokens.typography.pageTitle.letterSpacing,
    descriptionColor: semanticTokens.text.secondary,
    descriptionMaxWidth: "720px",
  },
} as const;

export const iconTokens = {
  size: foundationTokens.icon.size,
  strokeWidth: foundationTokens.icon.strokeWidth,
  color: semanticTokens.icon,
} as const;

type LayerConfig = {
  layer: ThemeTokenLayer;
  prefix: string;
  tokens: ThemeTokenTree;
};

const LAYER_CONFIGS: LayerConfig[] = [
  {
    layer: "foundation",
    prefix: "foundation",
    tokens: foundationTokens as ThemeTokenTree,
  },
  {
    layer: "semantic",
    prefix: "semantic",
    tokens: semanticTokens as ThemeTokenTree,
  },
  {
    layer: "component",
    prefix: "component",
    tokens: componentTokens as ThemeTokenTree,
  },
  { layer: "icon", prefix: "icon", tokens: iconTokens as ThemeTokenTree },
];

const legacyVariableMap: Record<`--${string}`, string> = {
  "--font-sans": foundationTokens.fontFamily.sans,
  "--font-display": foundationTokens.fontFamily.display,
  "--color-canvas": semanticTokens.background.pageBase,
  "--color-canvas-alt": semanticTokens.background.pageAlt,
  "--color-background-page-base": semanticTokens.background.pageBase,
  "--color-background-page-alt": semanticTokens.background.pageAlt,
  "--color-background-page-depth": semanticTokens.background.pageDepth,
  "--color-background-ambient-brand": semanticTokens.background.ambientBrand,
  "--color-background-ambient-info": semanticTokens.background.ambientInfo,
  "--color-surface": semanticTokens.surface.panel,
  "--color-surface-stage": semanticTokens.surface.stage,
  "--color-surface-panel": semanticTokens.surface.panel,
  "--color-surface-card": semanticTokens.surface.panel,
  "--color-surface-object": semanticTokens.surface.card,
  "--color-surface-muted": semanticTokens.surface.panelMuted,
  "--color-surface-card-muted": semanticTokens.surface.panelMuted,
  "--color-surface-soft": semanticTokens.surface.panelSoft,
  "--color-surface-card-soft": semanticTokens.surface.panelSoft,
  "--color-surface-strong": semanticTokens.surface.panelStrong,
  "--color-surface-card-strong": semanticTokens.surface.panelStrong,
  "--color-surface-brand": semanticTokens.surface.panelBrand,
  "--color-surface-workplane": semanticTokens.surface.workplane,
  "--color-surface-floating": semanticTokens.surface.floating,
  "--color-surface-card-overlay": semanticTokens.surface.panelOverlay,
  "--color-text": semanticTokens.text.primary,
  "--color-text-secondary": semanticTokens.text.secondary,
  "--color-text-tertiary": semanticTokens.text.tertiary,
  "--color-text-inverse": semanticTokens.text.inverse,
  "--color-border": semanticTokens.border.default,
  "--color-border-subtle": semanticTokens.border.subtle,
  "--color-border-strong": semanticTokens.border.strong,
  "--color-border-brand": semanticTokens.border.accent,
  "--color-brand-700": BRAND[700],
  "--color-brand-600": BRAND[600],
  "--color-brand-500": BRAND[500],
  "--color-brand-400": BRAND[400],
  "--color-brand-200": BRAND[200],
  "--color-brand-100": BRAND[100],
  "--color-success-600": STATUS.success[600],
  "--color-success-100": STATUS.success[100],
  "--color-warning-600": STATUS.warning[600],
  "--color-warning-100": STATUS.warning[100],
  "--color-danger-600": STATUS.danger[600],
  "--color-danger-100": STATUS.danger[100],
  "--color-info-600": STATUS.info[600],
  "--color-info-100": STATUS.info[100],
  "--shadow-sm": foundationTokens.shadow.sm,
  "--shadow-md": foundationTokens.shadow.md,
  "--shadow-lg": foundationTokens.shadow.lg,
  "--shadow-inset": foundationTokens.shadow.inset,
  "--radius-xs": foundationTokens.radius.xs,
  "--radius-sm": foundationTokens.radius.sm,
  "--radius-md": foundationTokens.radius.md,
  "--radius-lg": foundationTokens.radius.lg,
  "--radius-xl": foundationTokens.radius.xl,
  "--radius-full": foundationTokens.radius.full,
  "--space-1": foundationTokens.space[1],
  "--space-2": foundationTokens.space[2],
  "--space-3": foundationTokens.space[3],
  "--space-4": foundationTokens.space[4],
  "--space-5": foundationTokens.space[5],
  "--space-6": foundationTokens.space[6],
  "--space-7": foundationTokens.space[7],
  "--space-8": foundationTokens.space[8],
  "--space-9": foundationTokens.space[9],
  "--space-10": foundationTokens.space[10],
  "--sidebar-width": foundationTokens.layout.sidebarWidth,
  "--content-max-width": foundationTokens.layout.contentMaxWidth,
  "--transition-fast": foundationTokens.motion.fast,
  "--transition-base": foundationTokens.motion.base,
};

function toCssValue(value: ThemeTokenValue) {
  return typeof value === "number" ? String(value) : value;
}

function toKebabCase(value: string) {
  return value
    .replace(/([a-z0-9])([A-Z])/g, "$1-$2")
    .replace(/[ _/]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "")
    .toLowerCase();
}

function toLabel(path: string[]) {
  return path
    .map((segment) =>
      toKebabCase(segment)
        .split("-")
        .filter(Boolean)
        .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
        .join(" "),
    )
    .join(" / ");
}

function isTokenTree(
  value: ThemeTokenTree | ThemeTokenValue,
): value is ThemeTokenTree {
  return typeof value === "object" && value !== null;
}

function flattenTokenTree(
  layer: ThemeTokenLayer,
  prefix: string,
  tree: ThemeTokenTree,
  path: string[] = [],
): ThemeVariableDefinition[] {
  const variables: ThemeVariableDefinition[] = [];

  for (const [key, value] of Object.entries(tree)) {
    const nextPath = [...path, key];

    if (isTokenTree(value)) {
      variables.push(...flattenTokenTree(layer, prefix, value, nextPath));
      continue;
    }

    const name =
      `--${[prefix, ...nextPath].map(toKebabCase).join("-")}` as const;
    variables.push({
      name,
      value: toCssValue(value),
      layer,
      path: nextPath,
      label: toLabel(nextPath),
    });
  }

  return variables;
}

const layeredThemeTokenDefinitions = LAYER_CONFIGS.flatMap(
  ({ layer, prefix, tokens }) => flattenTokenTree(layer, prefix, tokens),
);

const legacyThemeTokenDefinitions: ThemeVariableDefinition[] = Object.entries(
  legacyVariableMap,
).map(([name, value]) => ({
  name: name as `--${string}`,
  value,
  layer: "foundation",
  path: [name],
  label: name,
  isAlias: true,
}));

export const themeTokenDefinitions = [
  ...layeredThemeTokenDefinitions,
  ...legacyThemeTokenDefinitions,
];

export const runtimeThemeTokenDefinitions = layeredThemeTokenDefinitions;

export function createThemeVariables(includeAliases = true): ThemeVariables {
  const definitions = includeAliases
    ? themeTokenDefinitions
    : runtimeThemeTokenDefinitions;

  return definitions.reduce<ThemeVariables>(
    (variables, definition) => ({
      ...variables,
      [definition.name]: definition.value,
    }),
    {} as ThemeVariables,
  );
}

export const themeVariables = createThemeVariables();

export function serializeThemeVars(
  variables: ThemeVariables = themeVariables,
  selector = ":root",
) {
  const body = Object.entries(variables)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([name, value]) => `  ${name}: ${value};`)
    .join("\n");

  return `${selector} {\n${body}\n}`;
}

const THEME_STYLE_ELEMENT_ID = "app-theme-variables";

let cachedSerializedThemeVars: string | null = null;

export function injectThemeVariables(doc: Document = document) {
  const targetDocument = doc;
  const head = targetDocument.head;

  if (!head) {
    return null;
  }

  const cssText = cachedSerializedThemeVars ?? serializeThemeVars();
  cachedSerializedThemeVars = cssText;

  let styleElement = targetDocument.getElementById(
    THEME_STYLE_ELEMENT_ID,
  ) as HTMLStyleElement | null;

  if (!styleElement) {
    styleElement = targetDocument.createElement("style");
    styleElement.id = THEME_STYLE_ELEMENT_ID;
    head.prepend(styleElement);
  }

  if (styleElement.textContent !== cssText) {
    styleElement.textContent = cssText;
  }

  return styleElement;
}

export function ensureThemeVariables(doc?: Document) {
  if (typeof document === "undefined") {
    return null;
  }

  return injectThemeVariables(doc ?? document);
}
