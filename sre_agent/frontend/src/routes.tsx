import React from "react";

import type { AppIconName } from "./components/ui";
import AlertsModifiedPage from "./pages/AlertsModified";
import DesignTokensPage from "./pages/DesignTokens";
import DiagnosisPage from "./pages/Diagnosis";
import KnowledgePage from "./pages/Knowledge";
import RemediationPage from "./pages/Remediation";
import SkillsPage from "./pages/Skills";
import TopologyPage from "./pages/Topology";

export type AppRoute = {
  key: string;
  path: string;
  label: string;
  section: "operations" | "assistant";
  icon: AppIconName;
  element: React.ReactElement;
};

export const appRoutes: AppRoute[] = [
  {
    key: "topology",
    path: "/topology",
    label: "拓扑",
    section: "operations",
    icon: "topology",
    element: <TopologyPage />,
  },
  {
    key: "alertsModified",
    path: "/alerts-modified",
    label: "告警",
    section: "operations",
    icon: "alerts",
    element: <AlertsModifiedPage />,
  },
  {
    key: "diagnosis",
    path: "/diagnosis",
    label: "诊断",
    section: "operations",
    icon: "diagnosis",
    element: <DiagnosisPage />,
  },
  {
    key: "remediation",
    path: "/remediation",
    label: "修复",
    section: "operations",
    icon: "remediation",
    element: <RemediationPage />,
  },
  {
    key: "knowledge",
    path: "/knowledge",
    label: "知识",
    section: "operations",
    icon: "knowledge",
    element: <KnowledgePage />,
  },
  {
    key: "skills",
    path: "/skills",
    label: "技能",
    section: "operations",
    icon: "skills",
    element: <SkillsPage />,
  },
  {
    key: "designTokens",
    path: "/design-tokens",
    label: "设计令牌",
    section: "assistant",
    icon: "spark",
    element: <DesignTokensPage />,
  },
];
