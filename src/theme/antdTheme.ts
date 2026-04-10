import type { ThemeConfig } from "antd";

import { componentTokens, foundationTokens, semanticTokens } from "./tokens";

function toNumber(value: string) {
  return Number.parseFloat(value);
}

export const appTheme: ThemeConfig = {
  token: {
    colorPrimary: semanticTokens.action.primary,
    colorInfo: semanticTokens.status.info,
    colorSuccess: semanticTokens.status.success,
    colorWarning: semanticTokens.status.warning,
    colorError: semanticTokens.status.danger,
    colorTextBase: semanticTokens.text.primary,
    colorBgBase: semanticTokens.background.pageBase,
    colorBorder: semanticTokens.border.default,
    borderRadius: toNumber(componentTokens.appInput.radius),
    borderRadiusLG: toNumber(componentTokens.surfaceCard.radius),
    boxShadow: foundationTokens.shadow.sm,
    boxShadowSecondary: foundationTokens.shadow.sm,
    fontFamily: foundationTokens.fontFamily.sans,
    controlOutline: semanticTokens.focus.ringSoft,
    colorFillAlter: semanticTokens.surface.panelSoft,
  },
  components: {
    Modal: {
      borderRadiusLG: toNumber(foundationTokens.radius.lg),
      contentBg: semanticTokens.surface.panel,
      headerBg: semanticTokens.surface.panel,
      titleColor: semanticTokens.text.primary,
      colorText: semanticTokens.text.primary,
      colorTextSecondary: semanticTokens.text.secondary,
      boxShadow: foundationTokens.shadow.md,
    },
    Tabs: {
      colorBorderSecondary: semanticTokens.border.default,
      colorPrimary: semanticTokens.action.primary,
      colorText: semanticTokens.text.secondary,
      colorTextHeading: semanticTokens.text.primary,
      itemSelectedColor: semanticTokens.text.primary,
      itemHoverColor: semanticTokens.text.primary,
      horizontalItemPadding: `${foundationTokens.space[2]} ${foundationTokens.space[3]}`,
      horizontalItemGutter: toNumber(foundationTokens.space[2]),
      inkBarColor: semanticTokens.action.primary,
      cardBg: semanticTokens.surface.panel,
      cardPadding: `${foundationTokens.space[2]} ${foundationTokens.space[3]}`,
    },
    Select: {
      activeBorderColor: semanticTokens.action.primary,
      hoverBorderColor: semanticTokens.border.strong,
      optionSelectedBg: semanticTokens.surface.panelSoft,
      optionActiveBg: semanticTokens.action.subtle,
      selectorBg: semanticTokens.surface.panel,
      colorBorder: semanticTokens.border.default,
      borderRadius: toNumber(componentTokens.appInput.radius),
      activeOutlineColor: semanticTokens.focus.ringSoft,
      multipleItemBg: semanticTokens.surface.panelSoft,
    },
    Input: {
      activeBorderColor: semanticTokens.action.primary,
      hoverBorderColor: semanticTokens.border.strong,
      activeShadow: `0 0 0 3px ${semanticTokens.focus.ringSoft}`,
    },
    Table: {
      headerBg: semanticTokens.surface.panelMuted,
      headerColor: semanticTokens.text.secondary,
      headerSplitColor: semanticTokens.border.default,
      borderColor: semanticTokens.border.default,
      rowHoverBg: semanticTokens.action.subtle,
      colorFillAlter: semanticTokens.surface.panelMuted,
      cellPaddingInline: toNumber(foundationTokens.space[4]),
      cellPaddingBlock: 12,
    },
    Tree: {
      nodeHoverBg: semanticTokens.action.subtle,
      nodeSelectedBg: semanticTokens.surface.panelSoft,
      colorText: semanticTokens.text.secondary,
      colorTextLightSolid: semanticTokens.text.primary,
      borderRadius: toNumber(componentTokens.appInput.radius),
    },
    Descriptions: {
      colorTextSecondary: semanticTokens.text.secondary,
      labelBg: "transparent",
    },
    List: {
      colorBorder: semanticTokens.border.default,
    },
  },
};
