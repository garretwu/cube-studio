import { XProvider } from "@ant-design/x";
import xZhCN from "@ant-design/x/locale/zh_CN";
import { App as AntApp, ConfigProvider } from "antd";
import antdZhCN from "antd/locale/zh_CN";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import App from "./App";
import "./styles.css";
import { appTheme } from "./theme/antdTheme";
import { ensureThemeVariables } from "./theme/tokens";

const xLocale = {
  ...antdZhCN,
  ...xZhCN,
};

async function enableMocks() {
  if (import.meta.env.DEV && import.meta.env.VITE_USE_MSW !== "false") {
    try {
      const { worker } = await import("./mocks/browser");
      await Promise.race([
        worker.start({ onUnhandledRequest: "bypass" }),
        new Promise((_, reject) => {
          window.setTimeout(() => {
            reject(new Error("MSW startup timeout"));
          }, 1500);
        }),
      ]);
    } catch (error) {
      console.warn("MSW 启动失败或超时，已切换为 API 本地 mock 回退。", error);
    }
  }
}

function renderApp() {
  ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
    <React.StrictMode>
      <ConfigProvider locale={antdZhCN} theme={appTheme}>
        <AntApp>
          <XProvider locale={xLocale} theme={appTheme}>
            <BrowserRouter>
              <App />
            </BrowserRouter>
          </XProvider>
        </AntApp>
      </ConfigProvider>
    </React.StrictMode>,
  );
}

function bootstrap() {
  ensureThemeVariables();
  renderApp();
  void enableMocks();
}

bootstrap();
