import React from "react";

import type { AppIconName } from "./components/ui";
import AlertsPage from "./pages/Alerts";
import ChatPage from "./pages/Chat";
import DesignTokensPage from "./pages/DesignTokens";
import DiagnosisPage from "./pages/Diagnosis";
import KnowledgePage from "./pages/Knowledge";
import MemoryPage from "./pages/Memory";
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
    key: "alerts",
    path: "/alerts",
    label: "告警",
    section: "operations",
    icon: "alerts",
    element: <AlertsPage />,
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
    key: "chat",
    path: "/chat",
    label: "对话",
    section: "assistant",
    icon: "chat",
    element: <ChatPage />,
  },
  {
    key: "knowledge",
    path: "/knowledge",
    label: "知识",
    section: "assistant",
    icon: "knowledge",
    element: <KnowledgePage />,
  },
  {
    key: "memory",
    path: "/memory",
    label: "记忆",
    section: "assistant",
    icon: "memory",
    element: <MemoryPage />,
  },
  {
    key: "skills",
    path: "/skills",
    label: "技能",
    section: "assistant",
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
