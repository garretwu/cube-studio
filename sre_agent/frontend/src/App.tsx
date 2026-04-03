import { useEffect, useMemo, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { apiClient } from "./api/client";
import type { DiagnosisSessionSummary } from "./api/types";
import { AppShell } from "./components/ui";
import { useAlertsRealtimeSync } from "./hooks/useAlertsRealtimeSync";
import HistoryPage from "./pages/History";
import DiagnosisPage from "./pages/Diagnosis";
import RemediationPage from "./pages/Remediation";
import SkillDetailPage from "./pages/SkillDetail";
import { appRoutes } from "./routes";

function resolveActiveRouteKey(pathname: string) {
  if (pathname === "/history" || pathname.startsWith("/history/")) {
    return "history";
  }

  const normalizedRoutes = [...appRoutes].sort((left, right) => right.path.length - left.path.length);
  return normalizedRoutes.find((route) => pathname === route.path || pathname.startsWith(`${route.path}/`))?.key ?? "topology";
}

function resolveHistoryChannelStatus(session: DiagnosisSessionSummary): "diagnosing" | "completed" {
  return ["resolved", "closed"].includes(session.status) ? "completed" : "diagnosing";
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/topology" replace />} />
      <Route path="/history" element={<HistoryPage />} />
      <Route path="/history/:sessionId" element={<DiagnosisPage />} />
      <Route path="/diagnosis" element={<DiagnosisPage />} />
      <Route path="/diagnosis/:sessionId" element={<DiagnosisPage />} />
      <Route path="/remediation/:sessionId" element={<RemediationPage />} />
      <Route path="/skills/:skillId" element={<SkillDetailPage />} />
      <Route path="/topology-modified" element={<Navigate to="/topology" replace />} />
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
  useAlertsRealtimeSync();
  const [historySessions, setHistorySessions] = useState<DiagnosisSessionSummary[]>([]);
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
          setHistoryLoadError(error instanceof Error ? error.message : "历史会话加载失败");
        }
      });

    return () => {
      cancelled = true;
    };
  }, [location.pathname]);

  const selected = resolveActiveRouteKey(location.pathname);
  const historySessionId = location.pathname.startsWith("/history/") ? location.pathname.split("/")[2] : undefined;
  const visibleRoutes = useMemo(() => appRoutes.filter((route) => route.key !== "designTokens"), []);
  const sections = [
    {
      title: "主导航",
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
      title: "历史频道",
      collapseBehavior: "hide" as const,
      items: historySessions.map((session) => ({
        key: session.session_id,
        label: session.title,
        active: session.session_id === historySessionId,
        kind: "history" as const,
        status: resolveHistoryChannelStatus(session),
        onClick: () => navigate(`/history/${session.session_id}`),
      })),
      emptyLabel: historyLoading
        ? "正在同步历史 session..."
        : historyLoadError
          ? "历史 session 加载失败"
          : "暂无历史 session",
    },
  ];

  return (
    <AppShell
      adminLabel="管理员"
      brandSubtitle="AIDC 智能运维指挥台"
      helpLabel="帮助中心"
      sections={sections}
      siteLabel="AIDC-001"
      userMeta="站点值班 / 智能运维"
      userName="Miaomiao Zhou"
      onBrandClick={() => navigate("/design-tokens")}
    >
      <AppRoutes />
    </AppShell>
  );
}

export default App;
export { resolveActiveRouteKey };
