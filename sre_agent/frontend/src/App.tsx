import { useEffect, useMemo, useState } from "react";
import {
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { Button, Modal, Space } from "antd";

import { apiClient } from "./api/client";
import type { DiagnosisSessionSummary } from "./api/types";
import {
  getAuthRecoveryState,
  hardReload,
  recoverAuthSession,
  startAuthSessionMonitor,
  subscribeAuthRecoveryState,
  type AuthRecoveryState,
} from "./auth/tokenManager";
import { AppShell } from "./components/ui";
import { useAlertsRealtimeSync } from "./hooks/useAlertsRealtimeSync";
import HistoryPage from "./pages/History";
import DiagnosisModifiedPage from "./pages/DiagnosisModified";
import KnowledgeDetailPage from "./pages/KnowledgeDetail";
import RemediationPage from "./pages/Remediation";
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
    contentWidthMode?: "default" | "full";
  }
> = {
  "/topology": {
    title: "AIDC档案",
    subtitle: "按 AIDC 资源、服务与上下游关系查看当前运行档案。",
    contentSpacing: "compact",
    contentMode: "workspace",
  },
  "/alerts": {
    title: "报警事件",
    subtitle: "聚合当前报警、影响对象与处置入口，便于快速分诊。",
    contentSpacing: "compact",
  },
  "/diagnosis": {
    title: "问题诊断",
    subtitle: "在统一工作台中追踪诊断推理、根因结论与修复流程。",
    contentSpacing: "compact",
    contentMode: "workspace",
    contentWidthMode: "full",
  },
  "/diagnosis-modified": {
    title: "问题诊断",
    subtitle: "在统一工作台中追踪诊断推理、根因结论与修复流程。",
    contentSpacing: "compact",
    contentMode: "workspace",
    contentWidthMode: "full",
  },
  "/remediation": {
    title: "修复记录",
    subtitle: "查看修复执行过程、审批结论与结果反馈。",
    contentSpacing: "compact",
  },
  "/knowledge": {
    title: "知识库",
    subtitle: "管理诊断与修复过程中使用的知识资产。",
    contentSpacing: "compact",
  },
  "/skills": {
    title: "技能管理",
    subtitle: "查看已接入平台的技能定义与说明。",
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
  contentWidthMode?: "default" | "full";
} {
  const rootChrome = rootPageChrome[pathname];
  if (rootChrome) {
    return rootChrome;
  }

  if (pathname.startsWith("/history/")) {
    return {
      title: "诊断历史",
      contentSpacing: "compact",
      contentMode: "workspace",
      contentWidthMode: "full",
    };
  }

  if (pathname.startsWith("/topology/")) {
    return {
      title: "AIDC档案",
      contentSpacing: "compact",
    };
  }

  if (pathname.startsWith("/alerts/")) {
    return {
      title: "报警事件",
      contentSpacing: "compact",
    };
  }

  if (pathname.startsWith("/diagnosis/")) {
    return {
      title: "问题诊断",
      contentSpacing: "compact",
      contentMode: "workspace",
      contentWidthMode: "full",
    };
  }

  if (pathname.startsWith("/diagnosis-modified/")) {
    return {
      title: "问题诊断",
      contentSpacing: "compact",
      contentMode: "workspace",
      contentWidthMode: "full",
    };
  }

  if (pathname.startsWith("/remediation/")) {
    return {
      title: "修复记录",
      contentSpacing: "compact",
    };
  }

  if (pathname.startsWith("/knowledge/")) {
    return {
      title: "知识库",
      contentSpacing: "compact",
    };
  }

  if (pathname.startsWith("/skills/")) {
    return {
      title: "技能管理",
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
      <Route path="/history/:sessionId" element={<DiagnosisModifiedPage />} />
      <Route path="/diagnosis" element={<DiagnosisModifiedPage />} />
      <Route path="/diagnosis/:sessionId" element={<DiagnosisModifiedPage />} />
      <Route path="/diagnosis-modified" element={<DiagnosisModifiedPage />} />
      <Route path="/diagnosis-modified/:sessionId" element={<DiagnosisModifiedPage />} />
      <Route path="/remediation/:sessionId" element={<RemediationPage />} />
      <Route path="/skills/:skillId" element={<SkillDetailPage />} />
      <Route
        path="/knowledge/:knowledgeBaseId"
        element={<KnowledgeDetailPage />}
      />
      <Route path="/topology/object/:nodeId/*" element={<TopologyObjectPage />} />
      <Route path="/topology-modified" element={<Navigate to="/topology" replace />} />
      <Route path="/topology-modified/object/:nodeId/*" element={<TopologyObjectPage />} />
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
  useAlertsRealtimeSync();
  const [historySessions, setHistorySessions] = useState<
    DiagnosisSessionSummary[]
  >([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyLoadError, setHistoryLoadError] = useState<string>("");
  const [authRecoveryState, setAuthRecoveryState] = useState<AuthRecoveryState>(() => getAuthRecoveryState());
  const [authRecoveryBusy, setAuthRecoveryBusy] = useState(false);

  useEffect(() => startAuthSessionMonitor(), []);
  useEffect(() => subscribeAuthRecoveryState(setAuthRecoveryState), []);

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
            error instanceof Error ? error.message : "加载诊断历史失败。",
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
      title: "导航",
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
      title: "诊断历史",
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
        ? "正在加载诊断会话..."
        : historyLoadError
          ? "诊断会话加载失败"
          : "暂无诊断会话",
    },
  ];

  const retryAuthRecovery = async () => {
    setAuthRecoveryBusy(true);
    try {
      await recoverAuthSession();
    } finally {
      setAuthRecoveryBusy(false);
    }
  };

  return (
    <>
      <AppShell
        adminLabel="SRE 控制台"
        brandSubtitle="AIDC 智能运维工作台"
        contentMode={pageChrome.contentMode}
        contentWidthMode={pageChrome.contentWidthMode}
        contentSpacing={pageChrome.contentSpacing}
        helpLabel="帮助文档"
        sections={sections}
        subtitle={pageChrome.subtitle}
        title={pageChrome.title}
        userMeta="平台团队 / Auto-SRE"
        userName="Miaomiao Zhou"
        onBrandClick={() => navigate("/design-tokens")}
      >
        <AppRoutes />
      </AppShell>
      <Modal
        title="认证会话已失效"
        open={authRecoveryState === "terminal"}
        closable={false}
        mask={{ closable: false }}
        footer={
          <Space>
            <Button onClick={() => hardReload()}>强制刷新页面</Button>
            <Button loading={authRecoveryBusy} onClick={() => void retryAuthRecovery()} type="primary">
              重新认证
            </Button>
          </Space>
        }
      >
        <p>检测到服务重启或签名密钥变化，当前会话无法自动恢复。</p>
        <p>请先尝试“重新认证”；若仍失败，请点击“强制刷新页面”。</p>
      </Modal>
    </>
  );
}

export default App;
export { resolveActiveRouteKey, resolvePageChrome };
