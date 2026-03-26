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
  25: "#f9fbff",
  50: "#f6f8fa",
  100: "#f1f4f9",
  200: "#e6e9ef",
  300: "#d7dde8",
  400: "#c4cad6",
  500: "#8b8fa3",
  600: "#6f7173",
  700: "#525662",
  800: "#1f2937",
  950: "#0d0d12",
} as const;

const STATUS = {
  success: {
    100: "#dff7ea",
    600: "#12b76a",
    700: "#0f8f51",
  },
  warning: {
    100: "#fff0cf",
    600: "#f59e0b",
    700: "#b46900",
  },
  danger: {
    100: "#ffe0dd",
    600: "#f04438",
    700: "#d92d20",
  },
  info: {
    100: "#def4ff",
    600: "#0ea5e9",
    700: "#0376b8",
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
    bodyLg: "15px",
    sectionTitle: "24px",
    pageTitle: "40px",
    metric: "34px",
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
    bodyLg: "24px",
    sectionTitle: "28px",
    pageTitle: "42px",
    metric: "34px",
    mono: "18px",
  },
  letterSpacing: {
    tight: "-0.04em",
    section: "-0.03em",
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
    sm: "12px",
    md: "14px",
    lg: "16px",
    xl: "20px",
    full: "999px",
  },
  shadow: {
    sm: "0 8px 24px rgba(15, 23, 42, 0.04)",
    md: "0 18px 40px rgba(15, 23, 42, 0.08)",
    lg: "0 28px 80px rgba(80, 61, 255, 0.12)",
    inset: "inset 0 1px 0 rgba(255, 255, 255, 0.7)",
  },
  layout: {
    sidebarWidth: "272px",
    contentMaxWidth: "1280px",
    metricTileMinHeight: "130px",
    controlHeightSm: "32px",
    controlHeightMd: "40px",
    controlHeightLg: "48px",
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
    pageDepth: "#eef2f6",
    ambientBrand: BRAND[100],
    ambientInfo: STATUS.info[100],
  },
  surface: {
    panel: NEUTRAL[0],
    panelMuted: NEUTRAL[50],
    panelSoft: NEUTRAL[100],
    panelStrong: NEUTRAL[200],
    panelBrand: BRAND[100],
    panelOverlay: "rgba(255, 255, 255, 0.92)",
    glass: "rgba(255, 255, 255, 0.84)",
  },
  border: {
    default: NEUTRAL[200],
    strong: NEUTRAL[300],
    subtle: "rgba(15, 23, 42, 0.08)",
    accent: "rgba(80, 61, 255, 0.2)",
    inverse: "rgba(255, 255, 255, 0.12)",
  },
  action: {
    primary: BRAND[600],
    primaryHover: BRAND[500],
    primaryActive: BRAND[700],
    subtle: "rgba(80, 61, 255, 0.08)",
    subtleHover: "rgba(80, 61, 255, 0.12)",
    selected: NEUTRAL[200],
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
    outline: "rgba(80, 61, 255, 0.32)",
    ring: "rgba(80, 61, 255, 0.24)",
    ringSoft: "rgba(80, 61, 255, 0.12)",
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
      border: semanticTokens.border.inverse,
      background:
        "linear-gradient(180deg, rgba(255, 255, 255, 0.16) 0%, rgba(255, 255, 255, 0) 100%), linear-gradient(90deg, #503dff 0%, #6658ff 100%)",
      shadow: "0 1px 2px rgba(14, 18, 27, 0.24), 0 0 0 1px rgba(80, 61, 255, 0.16)",
    },
    secondary: {
      text: semanticTokens.text.primary,
      border: semanticTokens.border.default,
      background: semanticTokens.surface.glass,
    },
    tertiary: {
      text: semanticTokens.text.secondary,
      border: "transparent",
      background: "transparent",
    },
    danger: {
      text: semanticTokens.status.danger,
      border: "rgba(240, 68, 56, 0.18)",
      background: "rgba(240, 68, 56, 0.08)",
    },
    disabledOpacity: "0.55",
  },
  appInput: {
    minHeight: foundationTokens.layout.controlHeightMd,
    radius: foundationTokens.radius.sm,
    background: semanticTokens.surface.panel,
    border: semanticTokens.border.default,
    shadow: foundationTokens.shadow.sm,
    text: semanticTokens.text.primary,
    adornment: semanticTokens.text.secondary,
    placeholder: semanticTokens.text.tertiary,
    focusBorder: semanticTokens.action.primaryHover,
    focusRing: `0 0 0 4px ${semanticTokens.focus.ringSoft}`,
    textareaMinHeight: "120px",
  },
  statusChip: {
    minHeight: "28px",
    paddingInline: "10px",
    gap: "6px",
    radius: foundationTokens.radius.full,
    fontSize: foundationTokens.fontSize.caption,
    fontWeight: foundationTokens.fontWeight.semibold,
    neutral: {
      text: semanticTokens.text.secondary,
      background: semanticTokens.surface.panelSoft,
    },
    accent: {
      text: semanticTokens.icon.accent,
      background: "rgba(80, 61, 255, 0.1)",
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
    border: "rgba(230, 233, 239, 0.88)",
    background: semanticTokens.surface.panel,
    shadow: foundationTokens.shadow.md,
    heroGlow: "none",
    softBackground: semanticTokens.surface.panelSoft,
    headerGap: foundationTokens.space[4],
    sectionPadding: foundationTokens.space[6],
  },
  sidebarNav: {
    brandMarkBackground:
      "linear-gradient(180deg, rgba(255, 255, 255, 0.16) 0%, rgba(255, 255, 255, 0) 100%), linear-gradient(90deg, #503dff 0%, #7266ff 100%)",
    brandMarkShadow: `${foundationTokens.shadow.inset}, 0 0 0 1px rgba(255, 255, 255, 0.12)`,
    sectionLabelColor: semanticTokens.text.primary,
    itemRadius: foundationTokens.radius.xs,
    itemText: semanticTokens.text.secondary,
    itemHoverText: semanticTokens.text.primary,
    itemHoverBackground: semanticTokens.action.subtle,
    itemActiveText: semanticTokens.text.primary,
    itemActiveBackground: semanticTokens.surface.panelStrong,
  },
  topbar: {
    compactHeight: foundationTokens.layout.controlHeightSm,
    compactPaddingInline: "12px",
    userBackground: "rgba(255, 255, 255, 0.9)",
    userBorder: semanticTokens.border.default,
    avatarBackground: "linear-gradient(135deg, #7cc4ff, #503dff)",
  },
  metricTile: {
    minHeight: foundationTokens.layout.metricTileMinHeight,
    radius: foundationTokens.radius.lg,
    border: "rgba(230, 233, 239, 0.88)",
    background: semanticTokens.surface.panel,
    shadow: foundationTokens.shadow.sm,
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
  { layer: "foundation", prefix: "foundation", tokens: foundationTokens as ThemeTokenTree },
  { layer: "semantic", prefix: "semantic", tokens: semanticTokens as ThemeTokenTree },
  { layer: "component", prefix: "component", tokens: componentTokens as ThemeTokenTree },
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
  "--color-surface-card": semanticTokens.surface.panel,
  "--color-surface-muted": semanticTokens.surface.panelMuted,
  "--color-surface-card-muted": semanticTokens.surface.panelMuted,
  "--color-surface-soft": semanticTokens.surface.panelSoft,
  "--color-surface-card-soft": semanticTokens.surface.panelSoft,
  "--color-surface-strong": semanticTokens.surface.panelStrong,
  "--color-surface-card-strong": semanticTokens.surface.panelStrong,
  "--color-surface-brand": semanticTokens.surface.panelBrand,
  "--color-surface-card-overlay": semanticTokens.surface.panelOverlay,
  "--color-text": semanticTokens.text.primary,
  "--color-text-secondary": semanticTokens.text.secondary,
  "--color-text-tertiary": semanticTokens.text.tertiary,
  "--color-text-inverse": semanticTokens.text.inverse,
  "--color-border": semanticTokens.border.default,
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

function isTokenTree(value: ThemeTokenTree | ThemeTokenValue): value is ThemeTokenTree {
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

    const name = `--${[prefix, ...nextPath].map(toKebabCase).join("-")}` as const;
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

const layeredThemeTokenDefinitions = LAYER_CONFIGS.flatMap(({ layer, prefix, tokens }) =>
  flattenTokenTree(layer, prefix, tokens),
);

const legacyThemeTokenDefinitions: ThemeVariableDefinition[] = Object.entries(legacyVariableMap).map(
  ([name, value]) => ({
    name: name as `--${string}`,
    value,
    layer: "foundation",
    path: [name],
    label: name,
    isAlias: true,
  }),
);

export const themeTokenDefinitions = [...layeredThemeTokenDefinitions, ...legacyThemeTokenDefinitions];

export const runtimeThemeTokenDefinitions = layeredThemeTokenDefinitions;

export function createThemeVariables(includeAliases = true): ThemeVariables {
  const definitions = includeAliases ? themeTokenDefinitions : runtimeThemeTokenDefinitions;

  return definitions.reduce<ThemeVariables>(
    (variables, definition) => ({
      ...variables,
      [definition.name]: definition.value,
    }),
    {} as ThemeVariables,
  );
}

export const themeVariables = createThemeVariables();

export function serializeThemeVars(variables: ThemeVariables = themeVariables, selector = ":root") {
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

  let styleElement = targetDocument.getElementById(THEME_STYLE_ELEMENT_ID) as HTMLStyleElement | null;

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
