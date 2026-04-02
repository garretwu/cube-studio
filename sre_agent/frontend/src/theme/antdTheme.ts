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
    boxShadow: foundationTokens.shadow.md,
    boxShadowSecondary: foundationTokens.shadow.sm,
    fontFamily: foundationTokens.fontFamily.sans,
  },
  components: {
    Modal: {
      borderRadiusLG: toNumber(foundationTokens.radius.lg),
      contentBg: semanticTokens.surface.panel,
      headerBg: semanticTokens.surface.panel,
      titleColor: semanticTokens.text.primary,
      colorText: semanticTokens.text.primary,
      colorTextSecondary: semanticTokens.text.secondary,
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
      cardBg: "transparent",
    },
    Select: {
      activeBorderColor: semanticTokens.action.primary,
      hoverBorderColor: semanticTokens.action.primaryHover,
      optionSelectedBg: semanticTokens.surface.panelBrand,
      optionActiveBg: semanticTokens.action.subtle,
      selectorBg: semanticTokens.surface.panel,
      colorBorder: semanticTokens.border.default,
      borderRadius: toNumber(componentTokens.appInput.radius),
    },
    Table: {
      headerBg: "transparent",
      headerColor: semanticTokens.text.secondary,
      headerSplitColor: semanticTokens.border.default,
      borderColor: semanticTokens.border.default,
      rowHoverBg: semanticTokens.action.subtle,
      colorFillAlter: "transparent",
      cellPaddingInline: toNumber(foundationTokens.space[4]),
      cellPaddingBlock: 14,
    },
    Tree: {
      nodeHoverBg: semanticTokens.action.subtle,
      nodeSelectedBg: semanticTokens.surface.panelBrand,
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
