import { useDeferredValue, useState } from "react";

import { AppButton, appIconCatalog, AppIcon, AppInput, SectionHeader, StatusChip, SurfaceCard } from "../components/ui";
import { useRuntimeDesignTokens } from "../hooks/useRuntimeDesignTokens";
import type { DesignTokenGroupKey, RuntimeDesignToken, RuntimeDesignTokenGroup } from "../theme/runtimeDesignTokens";

type GroupCopy = {
  title: string;
  description: string;
};

const GROUP_COPY: Record<DesignTokenGroupKey, GroupCopy> = {
  foundation: {
    title: "Foundation",
    description: "Raw primitives for color, spacing, radius, typography, layout, motion, and icon geometry.",
  },
  semantic: {
    title: "Semantic",
    description: "Intent-level aliases for text, surfaces, borders, actions, focus, status, and typography roles.",
  },
  component: {
    title: "Component",
    description: "Scoped tokens for AppButton, AppInput, StatusChip, SurfaceCard, SidebarNav, Topbar, and related primitives.",
  },
  icon: {
    title: "Icon",
    description: "Shared icon size, stroke, and color tokens consumed by the registry and catalog.",
  },
};

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function parseFirstNumber(value: string) {
  const match = value.match(/-?\d+(\.\d+)?/);
  return match ? Number(match[0]) : null;
}

function scaleMeasure(value: string, multiplier: number, min: number, max: number) {
  const numericValue = parseFirstNumber(value);

  if (numericValue === null) {
    return min;
  }

  return clamp(numericValue * multiplier, min, max);
}

function formatUpdatedAt(updatedAt: number) {
  return new Intl.DateTimeFormat("en", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(updatedAt);
}

function isColorLike(value: string) {
  return /^(#|rgb|hsl)/i.test(value) || value.includes("gradient(");
}

function isFontFamilyToken(name: string, value: string) {
  return name.includes("family") || value.includes("sans") || value.includes("mono") || value.includes("Inter");
}

function isShadowLike(name: string, value: string) {
  return name.includes("shadow") || (value.includes("rgba") && value.includes("px"));
}

function isRadiusLike(name: string) {
  return name.includes("radius");
}

function isMotionLike(name: string, value: string) {
  return name.includes("motion") || name.includes("transition") || value.includes("ease") || value.includes("ms");
}

function isMeasureLike(name: string, value: string) {
  return (
    name.includes("size") ||
    name.includes("space") ||
    name.includes("padding") ||
    name.includes("height") ||
    name.includes("width") ||
    /^\d+(\.\d+)?(px|rem|em)$/.test(value)
  );
}

function renderTokenPreview(token: RuntimeDesignToken) {
  if (isColorLike(token.value)) {
    return (
      <div className="token-demo token-demo--color">
        <span className="token-demo__swatch" style={{ background: token.value }} />
        <span className="token-demo__value">{token.value}</span>
      </div>
    );
  }

  if (isFontFamilyToken(token.name, token.value)) {
    return (
      <div className="token-demo token-demo--type" style={{ fontFamily: token.value }}>
        <span className="token-demo__glyph">Aa</span>
        <span className="token-demo__sample">Design system</span>
      </div>
    );
  }

  if (isRadiusLike(token.name)) {
    return (
      <div className="token-demo token-demo--radius">
        <span className="token-demo__shape token-demo__shape--radius" style={{ borderRadius: token.value }} />
        <span className="token-demo__value">{token.value}</span>
      </div>
    );
  }

  if (isShadowLike(token.name, token.value)) {
    return (
      <div className="token-demo token-demo--shadow">
        <span className="token-demo__shape token-demo__shape--shadow" style={{ boxShadow: token.value }} />
        <span className="token-demo__value">{token.value}</span>
      </div>
    );
  }

  if (isMotionLike(token.name, token.value)) {
    return (
      <div className="token-demo token-demo--motion">
        <span
          className="token-demo__motion-pill"
          style={{ transition: `transform ${token.value}, box-shadow ${token.value}` }}
        />
        <span className="token-demo__value">{token.value}</span>
      </div>
    );
  }

  if (isMeasureLike(token.name, token.value)) {
    return (
      <div className="token-demo token-demo--measure">
        <span className="token-demo__track">
          <span className="token-demo__bar" style={{ width: scaleMeasure(token.value, 3.5, 24, 220) }} />
        </span>
        <span className="token-demo__value">{token.value}</span>
      </div>
    );
  }

  return (
    <div className="token-demo token-demo--fallback">
      <span className="token-demo__code">{`var(${token.name})`}</span>
      <span className="token-demo__value">{token.value}</span>
    </div>
  );
}

function renderGroupAccent(group: RuntimeDesignTokenGroup) {
  const leadToken = group.tokens.find((token) => isColorLike(token.value)) ?? group.tokens[0];

  if (!leadToken) {
    return null;
  }

  if (isColorLike(leadToken.value)) {
    return (
      <span className="token-summary-card__accent token-summary-card__accent--swatch" style={{ background: leadToken.value }} />
    );
  }

  if (isShadowLike(leadToken.name, leadToken.value)) {
    return (
      <span className="token-summary-card__accent token-summary-card__accent--panel" style={{ boxShadow: leadToken.value }} />
    );
  }

  if (isRadiusLike(leadToken.name)) {
    return (
      <span
        className="token-summary-card__accent token-summary-card__accent--panel"
        style={{ borderRadius: leadToken.value }}
      />
    );
  }

  return <span className="token-summary-card__accent token-summary-card__accent--text">{leadToken.label}</span>;
}

function matchesTokenQuery(token: RuntimeDesignToken, query: string) {
  if (!query) {
    return true;
  }

  return (
    token.name.toLowerCase().includes(query) ||
    token.label.toLowerCase().includes(query) ||
    token.value.toLowerCase().includes(query)
  );
}

function DesignTokensPage() {
  const [query, setQuery] = useState("");
  const [iconQuery, setIconQuery] = useState("");
  const deferredQuery = useDeferredValue(query);
  const deferredIconQuery = useDeferredValue(iconQuery);
  const { groups, tokens, updatedAt, refresh } = useRuntimeDesignTokens();
  const normalizedQuery = deferredQuery.trim().toLowerCase();
  const normalizedIconQuery = deferredIconQuery.trim().toLowerCase();

  const visibleGroups = groups
    .map((group) => ({
      ...group,
      tokens: group.tokens.filter((token) => matchesTokenQuery(token, normalizedQuery)),
    }))
    .filter((group) => group.tokens.length > 0);

  const visibleTokenCount = visibleGroups.reduce((count, group) => count + group.tokens.length, 0);

  const visibleIcons = appIconCatalog.filter((icon) => {
    if (!normalizedIconQuery) {
      return true;
    }

    return (
      icon.name.toLowerCase().includes(normalizedIconQuery) ||
      icon.aliases.some((alias) => alias.toLowerCase().includes(normalizedIconQuery)) ||
      icon.keywords.some((keyword) => keyword.toLowerCase().includes(normalizedIconQuery))
    );
  });

  return (
    <div className="page-grid token-page">
      <div className="page-intro">
        <SectionHeader
          actions={
            <div className="token-hero__status">
              <StatusChip tone="accent">Shared TS source</StatusChip>
              <StatusChip tone="info">{`${visibleTokenCount || tokens.length} tokens`}</StatusChip>
              <StatusChip tone="neutral">{`${appIconCatalog.length} local icons`}</StatusChip>
              <StatusChip tone="neutral">{`Updated ${formatUpdatedAt(updatedAt)}`}</StatusChip>
            </div>
          }
          eyebrow="Runtime Design System"
          title="Global Token Explorer"
          description="This page reads live CSS custom properties at runtime, so foundation, semantic, component, and icon updates stay in sync with the actual app."
        />

        <SurfaceCard bodyClassName="page-stack" className="token-hero" variant="hero">
          <div className="token-toolbar">
            <AppInput
              onChange={setQuery}
              placeholder="Filter token name, label, or value"
              prefix={<AppIcon name="search" size={16} />}
              value={query}
            />
            <div className="token-toolbar__actions">
              <AppButton iconLeft="refresh" onClick={refresh} size="sm" variant="secondary">
                Refresh
              </AppButton>
            </div>
          </div>

          <div className="card-grid--three">
            {groups.map((group) => (
              <div key={group.key} className="token-summary-card">
                <div className="token-summary-card__header">
                  <div>
                    <p className="token-summary-card__eyebrow">{GROUP_COPY[group.key].title}</p>
                    <p className="token-summary-card__count">{group.tokens.length}</p>
                  </div>
                  {renderGroupAccent(group)}
                </div>
                <p className="token-summary-card__description">{GROUP_COPY[group.key].description}</p>
              </div>
            ))}
          </div>

          <p className="token-hero__footnote">
            Variables are serialized from TypeScript and injected into <code>:root</code> at bootstrap.
          </p>
        </SurfaceCard>
      </div>

      {visibleGroups.length > 0 ? (
        visibleGroups.map((group) => (
          <SurfaceCard
            key={group.key}
            actions={<StatusChip tone="neutral">{`${group.tokens.length} tokens`}</StatusChip>}
            className="token-group-card"
            description={GROUP_COPY[group.key].description}
            title={GROUP_COPY[group.key].title}
          >
            <div className="token-grid">
              {group.tokens.map((token) => (
                <article key={token.name} className="token-card">
                  <div className="token-card__header">
                    <div className="token-card__meta">
                      <p className="token-card__name">{token.label}</p>
                      <p className="token-card__reference">{token.name}</p>
                    </div>
                    <p className="token-card__current">{token.value}</p>
                  </div>
                  {renderTokenPreview(token)}
                </article>
              ))}
            </div>
          </SurfaceCard>
        ))
      ) : (
        <SurfaceCard className="token-empty" variant="soft">
          <SectionHeader
            align="center"
            eyebrow="No Tokens"
            title="Nothing matched this token query"
            description="Try a broader search term or clear the filter to inspect the entire layered token surface."
          />
        </SurfaceCard>
      )}

      <SurfaceCard
        actions={<StatusChip tone="neutral">{`${visibleIcons.length} icons`}</StatusChip>}
        description="The local TSX registry keeps `name + variant` stable, while legacy names continue to resolve through aliases."
        title="Icon Catalog"
      >
        <div className="icon-catalog__toolbar">
          <AppInput
            onChange={setIconQuery}
            placeholder="Search icon name, alias, or keyword"
            prefix={<AppIcon name="search" size={16} />}
            value={iconQuery}
          />
        </div>

        {visibleIcons.length > 0 ? (
          <div className="icon-grid">
            {visibleIcons.map((icon) => (
              <article key={icon.name} className="icon-card">
                <div className="icon-card__header">
                  <p className="icon-card__title">{icon.name}</p>
                  <p className="icon-card__aliases">
                    {icon.aliases.length > 0 ? `Aliases: ${icon.aliases.join(", ")}` : "Aliases: none"}
                  </p>
                </div>

                <div className="icon-card__variants">
                  <div className="icon-card__variant">
                    <p className="icon-card__label">Outline</p>
                    <div className="icon-card__preview">
                      <AppIcon name={icon.name} size={20} variant="outline" />
                    </div>
                  </div>
                  <div className="icon-card__variant">
                    <p className="icon-card__label">Fill</p>
                    <div className="icon-card__preview icon-card__preview--fill">
                      <AppIcon name={icon.name} size={20} variant="fill" />
                    </div>
                  </div>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <SectionHeader
            align="center"
            eyebrow="No Icons"
            title="Nothing matched this icon query"
            description="Search by canonical name, legacy alias, or icon keyword to validate coverage."
          />
        )}
      </SurfaceCard>
    </div>
  );
}

export default DesignTokensPage;
