import React from "react";
import {
  ApartmentOutlined,
  AlertOutlined,
  ApiOutlined,
  BookOutlined,
  CommentOutlined,
  DeploymentUnitOutlined,
  ExperimentOutlined,
  ToolOutlined,
} from "@ant-design/icons";

import AlertsPage from "./pages/Alerts";
import ChatPage from "./pages/Chat";
import DiagnosisPage from "./pages/Diagnosis";
import KnowledgePage from "./pages/Knowledge";
import MemoryPage from "./pages/Memory";
import RemediationPage from "./pages/Remediation";
import SkillsPage from "./pages/Skills";
import TopologyPage from "./pages/Topology";

export type AppRoute = {
  key: string;
  path: string;
  labelKey: string;
  icon: React.ReactNode;
  element: React.ReactElement;
};

export const appRoutes: AppRoute[] = [
  {
    key: "topology",
    path: "/topology",
    labelKey: "nav.topology",
    icon: <ApartmentOutlined />,
    element: <TopologyPage />,
  },
  {
    key: "alerts",
    path: "/alerts",
    labelKey: "nav.alerts",
    icon: <AlertOutlined />,
    element: <AlertsPage />,
  },
  {
    key: "diagnosis",
    path: "/diagnosis",
    labelKey: "nav.diagnosis",
    icon: <ApiOutlined />,
    element: <DiagnosisPage />,
  },
  {
    key: "remediation",
    path: "/remediation",
    labelKey: "nav.remediation",
    icon: <DeploymentUnitOutlined />,
    element: <RemediationPage />,
  },
  {
    key: "chat",
    path: "/chat",
    labelKey: "nav.chat",
    icon: <CommentOutlined />,
    element: <ChatPage />,
  },
  {
    key: "knowledge",
    path: "/knowledge",
    labelKey: "nav.knowledge",
    icon: <BookOutlined />,
    element: <KnowledgePage />,
  },
  {
    key: "memory",
    path: "/memory",
    labelKey: "nav.memory",
    icon: <ExperimentOutlined />,
    element: <MemoryPage />,
  },
  {
    key: "skills",
    path: "/skills",
    labelKey: "nav.skills",
    icon: <ToolOutlined />,
    element: <SkillsPage />,
  },
];
