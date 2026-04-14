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
    title: "拓扑",
    subtitle: "展示实体关系与依赖路径",
    contentSpacing: "compact",
    contentMode: "workspace",
  },
  "/topology-modified": {
    title: "拓扑（修改）",
    subtitle: "沿用当前拓扑数据的新视觉版本",
    contentSpacing: "compact",
    contentMode: "workspace",
  },
  "/alerts": {
    title: "告警",
    subtitle: "查看异常告警并进入诊断处置",
    contentSpacing: "compact",
  },
  "/diagnosis": {
    title: "诊断",
    subtitle: "基于证据链收敛根因结论",
    contentSpacing: "compact",
    contentMode: "workspace",
  },
  "/remediation": {
    title: "修复",
    subtitle: "汇总修复方案并跟踪执行状态",
    contentSpacing: "compact",
  },
  "/knowledge": {
    title: "知识",
    subtitle: "统一检索运维知识与操作手册",
    contentSpacing: "compact",
  },
  "/skills": {
    title: "技能",
    subtitle: "管理智能技能与当前可用能力",
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
      title: "诊断",
      contentSpacing: "compact",
      contentMode: "workspace",
    };
  }

  if (pathname.startsWith("/topology/")) {
    return {
      title: "拓扑",
      contentSpacing: "compact",
    };
  }

  if (pathname.startsWith("/alerts/")) {
    return {
      title: "告警",
      contentSpacing: "compact",
    };
  }

  if (pathname.startsWith("/diagnosis/")) {
    return {
      title: "诊断",
      contentSpacing: "compact",
      contentMode: "workspace",
    };
  }

  if (pathname.startsWith("/remediation/")) {
    return {
      title: "修复",
      contentSpacing: "compact",
    };
  }

  if (pathname.startsWith("/knowledge/")) {
    return {
      title: "知识",
      contentSpacing: "compact",
    };
  }

  if (pathname.startsWith("/skills/")) {
    return {
      title: "技能",
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
      <Route path="/topology/object/:nodeId" element={<TopologyObjectPage />} />
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
            error instanceof Error ? error.message : "历史会话加载失败",
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
        label: session.alert_name,
        active: session.session_id === historySessionId,
        kind: "history" as const,
        metaLabel: session.severity.toUpperCase(),
        metaTone: session.severity,
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
      contentMode={pageChrome.contentMode}
      contentSpacing={pageChrome.contentSpacing}
      helpLabel="帮助中心"
      sections={sections}
      subtitle={pageChrome.subtitle}
      title={pageChrome.title}
      userMeta="站点值班 / 智能运维"
      userName="Miaomiao Zhou"
      onBrandClick={() => navigate("/design-tokens")}
    >
      <AppRoutes />
    </AppShell>
  );
}

export default App;
export { resolveActiveRouteKey, resolvePageChrome };

