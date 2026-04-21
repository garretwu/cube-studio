import React from "react";

import type { AppIconName } from "./components/ui";
import AlertsModifiedPage from "./pages/AlertsModified";
import DesignTokensPage from "./pages/DesignTokens";
import DiagnosisPage from "./pages/Diagnosis";
import DiagnosisModifiedPage from "./pages/DiagnosisModified";
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
    label: "AIDC档案",
    section: "operations",
    icon: "topology",
    element: <TopologyPage variant="modified" />,
  },
  {
    key: "alertsModified",
    path: "/alerts",
    label: "报警事件",
    section: "operations",
    icon: "alerts",
    element: <AlertsModifiedPage />,
  },
  {
    key: "diagnosis",
    path: "/diagnosis",
    label: "问题诊断",
    section: "operations",
    icon: "diagnosis",
    element: <DiagnosisPage />,
  },
  {
    key: "diagnosisModified",
    path: "/diagnosis-modified",
    label: "问题诊断（修改）",
    section: "operations",
    icon: "diagnosis",
    element: <DiagnosisModifiedPage />,
  },
  {
    key: "remediation",
    path: "/remediation",
    label: "修复记录",
    section: "operations",
    icon: "remediation",
    element: <RemediationPage />,
  },
  {
    key: "knowledge",
    path: "/knowledge",
    label: "知识库",
    section: "operations",
    icon: "knowledge",
    element: <KnowledgePage />,
  },
  {
    key: "skills",
    path: "/skills",
    label: "技能管理",
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
