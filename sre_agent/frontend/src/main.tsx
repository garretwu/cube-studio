import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App as AntApp, ConfigProvider } from "antd";

import App from "./App";
import "./styles.css";
import { appTheme } from "./theme/antdTheme";
import { ensureThemeVariables } from "./theme/tokens";

async function enableMocks() {
  if (!import.meta.env.DEV || import.meta.env.VITE_USE_MSW !== "true") {
    return;
  }
  try {
    const { worker } = await import("./mocks/browser");
    await worker.start({ onUnhandledRequest: "bypass" });
  } catch (error) {
    console.warn("Failed to start MSW, continue with real backend.", error);
  }
}

async function bootstrap() {
  ensureThemeVariables();
  await enableMocks();

  ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
    <React.StrictMode>
      <ConfigProvider theme={appTheme}>
        <AntApp>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </AntApp>
      </ConfigProvider>
    </React.StrictMode>,
  );
}

void bootstrap();
