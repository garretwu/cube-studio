import React from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { AppShell } from "./components/ui";
import { appRoutes } from "./routes";

function App() {
  const navigate = useNavigate();
  const location = useLocation();

  const selected = appRoutes.find((route) => location.pathname.startsWith(route.path))?.key ?? "topology";
  const sections = [
    {
      title: "主导航",
      items: appRoutes
        .filter((route) => route.section === "operations")
        .map((route) => ({
          key: route.key,
          label: route.label,
          icon: route.icon,
          active: route.key === selected,
          onClick: () => navigate(route.path),
        })),
    },
    {
      title: "智能协作",
      items: appRoutes
        .filter((route) => route.section === "assistant" && route.key !== "designTokens")
        .map((route) => ({
          key: route.key,
          label: route.label,
          icon: route.icon,
          active: route.key === selected,
          onClick: () => navigate(route.path),
        })),
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
      <Routes>
        <Route path="/" element={<Navigate to="/topology" replace />} />
        {appRoutes.map((route) => (
          <Route key={route.key} path={route.path} element={route.element} />
        ))}
      </Routes>
    </AppShell>
  );
}

export default App;
