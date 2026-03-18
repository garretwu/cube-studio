import React from "react";
import { Layout, Menu, Segmented, Space, Tag, Typography } from "antd";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { appRoutes } from "./routes";

const { Header, Sider, Content } = Layout;
const { Title, Text } = Typography;

function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const { t, i18n } = useTranslation();

  const selected = appRoutes.find((route) => location.pathname.startsWith(route.path))?.key ?? "topology";

  return (
    <Layout className="shell">
      <Sider width={272} theme="light" className="shell-sider">
        <div className="brand">
          <div className="brand-mark">AE</div>
          <div>
            <Title level={4} className="brand-title">
              AIDC Auto-SRE
            </Title>
            <Text className="brand-subtitle">{t("common.commandCenter")}</Text>
          </div>
        </div>
        <Menu
          selectedKeys={[selected]}
          mode="inline"
          className="shell-menu"
          items={appRoutes.map((route) => ({
            key: route.key,
            icon: route.icon,
            label: t(route.labelKey),
            onClick: () => navigate(route.path),
          }))}
        />
      </Sider>
      <Layout>
        <Header className="shell-header">
          <div>
            <Title level={3} className="shell-heading">
              {t("common.commandCenter")}
            </Title>
            <Text className="shell-copy">{t("common.subtitle")}</Text>
          </div>
          <Space size="middle">
            <Segmented
              value={i18n.language}
              options={[
                { label: "中文", value: "zh" },
                { label: "EN", value: "en" },
              ]}
              onChange={(value) => void i18n.changeLanguage(String(value))}
            />
            <Tag color="cyan">aidc-001</Tag>
            <Tag color="geekblue">{t("common.admin")}</Tag>
          </Space>
        </Header>
        <Content className="shell-content">
          <Routes>
            <Route path="/" element={<Navigate to="/topology" replace />} />
            {appRoutes.map((route) => (
              <Route key={route.key} path={route.path} element={route.element} />
            ))}
          </Routes>
        </Content>
      </Layout>
    </Layout>
  );
}

export default App;
