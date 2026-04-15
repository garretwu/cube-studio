import { useEffect, useMemo, useState } from "react";
import {
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";

import { apiClient } from "./api/client";
import type { DiagnosisSessionSummary } from "./api/types";
import { AppShell } from "./components/ui";
import HistoryPage from "./pages/History";
import DiagnosisPage from "./pages/Diagnosis";
import KnowledgeDetailPage from "./pages/KnowledgeDetail";
import SkillDetailPage from "./pages/SkillDetail";
import TopologyObjectPage from "./pages/TopologyObject";
import { appRoutes } from "./routes";

const rootPageChrome: Record<
  string,
  {
    title: string;
    subtitle: string;
    contentSpacing: "compact";
    contentMode?: "default" | "workspace";
  }
> = {
  "/topology": {
    title: "Topology",
    subtitle: "View service topology and dependency status in real time.",
    contentSpacing: "compact",
    contentMode: "workspace",
  },
  "/topology-modified": {
    title: "Topology (Modified)",
    subtitle: "Compare routing and layout changes in the modified topology view.",
    contentSpacing: "compact",
    contentMode: "workspace",
  },
  "/alerts": {
    title: "Alerts",
    subtitle: "Track alert signals, severity, and ownership for ongoing incidents.",
    contentSpacing: "compact",
  },
  "/diagnosis": {
    title: "Diagnosis",
    subtitle: "Follow the diagnosis workflow and remediation execution timeline.",
    contentSpacing: "compact",
    contentMode: "workspace",
  },
  "/remediation": {
    title: "Remediation",
    subtitle: "Review remediation plans, execution status, and rollback options.",
    contentSpacing: "compact",
  },
  "/knowledge": {
    title: "Knowledge",
    subtitle: "Browse troubleshooting knowledge and reusable incident playbooks.",
    contentSpacing: "compact",
  },
  "/skills": {
    title: "Skills",
    subtitle: "Manage diagnostic skills and monitor skill execution insights.",
    contentSpacing: "compact",
  },
};

function resolveActiveRouteKey(pathname: string) {
  if (pathname === "/history" || pathname.startsWith("/history/")) {
    return "history";
  }

  const normalizedRoutes = [...appRoutes].sort(
    (left, right) => right.path.length - left.path.length,
  );
  return (
    normalizedRoutes.find(
      (route) =>
        pathname === route.path || pathname.startsWith(`${route.path}/`),
    )?.key ?? "topology"
  );
}

function resolveHistoryChannelStatus(
  session: DiagnosisSessionSummary,
): "diagnosing" | "completed" {
  return ["resolved", "closed"].includes(session.status)
    ? "completed"
    : "diagnosing";
}

function resolvePageChrome(pathname: string): {
  title?: string;
  subtitle?: string;
  contentSpacing?: "default" | "compact";
  contentMode?: "default" | "workspace";
} {
  const rootChrome = rootPageChrome[pathname];
  if (rootChrome) {
    return rootChrome;
  }

  if (pathname.startsWith("/history/")) {
    return {
      title: "Diagnosis",
      contentSpacing: "compact",
      contentMode: "workspace",
    };
  }

  if (pathname.startsWith("/topology/")) {
    return {
      title: "Topology",
      contentSpacing: "compact",
    };
  }

  if (pathname.startsWith("/alerts/")) {
    return {
      title: "Alerts",
      contentSpacing: "compact",
    };
  }

  if (pathname.startsWith("/diagnosis/")) {
    return {
      title: "Diagnosis",
      contentSpacing: "compact",
      contentMode: "workspace",
    };
  }

  if (pathname.startsWith("/remediation/")) {
    return {
      title: "Remediation",
      contentSpacing: "compact",
    };
  }

  if (pathname.startsWith("/knowledge/")) {
    return {
      title: "Knowledge",
      contentSpacing: "compact",
    };
  }

  if (pathname.startsWith("/skills/")) {
    return {
      title: "Skills",
      contentSpacing: "compact",
    };
  }

  return {};
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/topology" replace />} />
      <Route path="/history" element={<HistoryPage />} />
      <Route path="/history/:sessionId" element={<DiagnosisPage />} />
      <Route path="/diagnosis" element={<DiagnosisPage />} />
      <Route path="/diagnosis/:sessionId" element={<DiagnosisPage />} />
      <Route path="/skills/:skillId" element={<SkillDetailPage />} />
      <Route
        path="/knowledge/:knowledgeBaseId"
        element={<KnowledgeDetailPage />}
      />
      <Route path="/topology/object/:nodeId/*" element={<TopologyObjectPage />} />
      <Route
        path="/alerts-modified"
        element={<Navigate to="/alerts" replace />}
      />
      {appRoutes
        .filter((route) => route.key !== "diagnosis")
        .map((route) => (
          <Route key={route.key} path={route.path} element={route.element} />
        ))}
    </Routes>
  );
}

function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const [historySessions, setHistorySessions] = useState<
    DiagnosisSessionSummary[]
  >([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyLoadError, setHistoryLoadError] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    setHistoryLoading(true);
    setHistoryLoadError("");
    void apiClient
      .getDiagnosisHistorySessions()
      .then((loadedSessions) => {
        if (!cancelled) {
          setHistorySessions(loadedSessions);
          setHistoryLoading(false);
          setHistoryLoadError("");
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setHistorySessions([]);
          setHistoryLoading(false);
          setHistoryLoadError(
            error instanceof Error ? error.message : "Failed to load diagnosis history.",
          );
        }
      });

    return () => {
      cancelled = true;
    };
  }, [location.pathname]);

  const selected = resolveActiveRouteKey(location.pathname);
  const historySessionId = location.pathname.startsWith("/history/")
    ? location.pathname.split("/")[2]
    : undefined;
  const visibleRoutes = useMemo(
    () => appRoutes.filter((route) => route.key !== "designTokens"),
    [],
  );
  const pageChrome = useMemo(
    () => resolvePageChrome(location.pathname),
    [location.pathname],
  );
  const sections = [
    {
      title: "Navigation",
      collapseBehavior: "icon-only" as const,
      items: visibleRoutes.map((route) => ({
        key: route.key,
        label: route.label,
        icon: route.icon,
        active: route.key === selected,
        onClick: () => navigate(route.path),
      })),
    },
    {
      title: "Diagnosis History",
      collapseBehavior: "hide" as const,
      items: historySessions.map((session) => ({
        key: session.session_id,
        label: session.alert_name,
        active: session.session_id === historySessionId,
        kind: "history" as const,
        metaLabel: session.severity.toUpperCase(),
        metaTone: session.severity,
        status: resolveHistoryChannelStatus(session),
        onClick: () => navigate(`/history/${session.session_id}`),
      })),
      emptyLabel: historyLoading
        ? "Loading diagnosis sessions..."
        : historyLoadError
          ? "Failed to load diagnosis sessions"
          : "No diagnosis sessions yet",
    },
  ];

  return (
    <AppShell
      adminLabel="SRE Console"
      brandSubtitle="AIDC Intelligent Ops Workspace"
      contentMode={pageChrome.contentMode}
      contentSpacing={pageChrome.contentSpacing}
      helpLabel="Help & Docs"
      sections={sections}
      subtitle={pageChrome.subtitle}
      title={pageChrome.title}
      userMeta="Platform Team / Auto-SRE"
      userName="Miaomiao Zhou"
      onBrandClick={() => navigate("/design-tokens")}
    >
      <AppRoutes />
    </AppShell>
  );
}

export default App;
export { resolveActiveRouteKey, resolvePageChrome };