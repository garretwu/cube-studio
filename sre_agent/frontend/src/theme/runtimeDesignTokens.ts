import {
  ensureThemeVariables,
  runtimeThemeTokenDefinitions,
  type ThemeTokenLayer,
  type ThemeVariableDefinition,
} from "./tokens";

export type DesignTokenGroupKey = ThemeTokenLayer;

export type RuntimeDesignToken = {
  name: string;
  label: string;
  value: string;
  group: DesignTokenGroupKey;
};

export type RuntimeDesignTokenGroup = {
  key: DesignTokenGroupKey;
  tokens: RuntimeDesignToken[];
};

export type RuntimeDesignTokenSnapshot = {
  tokens: RuntimeDesignToken[];
  groups: RuntimeDesignTokenGroup[];
  signature: string;
  updatedAt: number;
};

const GROUP_ORDER: DesignTokenGroupKey[] = ["foundation", "semantic", "component", "icon"];

function resolveRoot(root?: HTMLElement | null) {
  if (root) {
    return root;
  }

  if (typeof document === "undefined") {
    return null;
  }

  return document.documentElement;
}

function normalizeValue(value: string) {
  return value.trim().replace(/\s+/g, " ");
}

function toRuntimeToken(definition: ThemeVariableDefinition, computedStyle: CSSStyleDeclaration): RuntimeDesignToken {
  return {
    name: definition.name,
    label: definition.label,
    value: normalizeValue(computedStyle.getPropertyValue(definition.name)),
    group: definition.layer,
  };
}

export function readRuntimeDesignTokens(root?: HTMLElement | null): RuntimeDesignToken[] {
  const resolvedRoot = resolveRoot(root);

  if (!resolvedRoot) {
    return [];
  }

  ensureThemeVariables(resolvedRoot.ownerDocument ?? document);

  const computedStyle = getComputedStyle(resolvedRoot);

  return runtimeThemeTokenDefinitions
    .map((definition) => toRuntimeToken(definition, computedStyle))
    .filter((token) => token.value.length > 0);
}

export function groupRuntimeDesignTokens(tokens: RuntimeDesignToken[]): RuntimeDesignTokenGroup[] {
  const groupedTokens = new Map<DesignTokenGroupKey, RuntimeDesignToken[]>();

  for (const token of tokens) {
    const currentTokens = groupedTokens.get(token.group) ?? [];
    currentTokens.push(token);
    groupedTokens.set(token.group, currentTokens);
  }

  return GROUP_ORDER.map((key) => ({
    key,
    tokens: (groupedTokens.get(key) ?? []).sort((left, right) =>
      left.name.localeCompare(right.name, undefined, { numeric: true }),
    ),
  })).filter((group) => group.tokens.length > 0);
}

export function createRuntimeDesignTokenSnapshot(root?: HTMLElement | null): RuntimeDesignTokenSnapshot {
  const tokens = readRuntimeDesignTokens(root);
  const groups = groupRuntimeDesignTokens(tokens);

  return {
    tokens,
    groups,
    signature: tokens.map((token) => `${token.name}:${token.value}`).join("|"),
    updatedAt: Date.now(),
  };
}
