import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App as AntApp, ConfigProvider, theme } from "antd";

import App from "./App";
import "./i18n";
import "./styles.css";

async function enableMocks() {
  if (import.meta.env.DEV && import.meta.env.VITE_USE_MSW === "true") {
    const { worker } = await import("./mocks/browser");
    await worker.start({ onUnhandledRequest: "bypass" });
  }
}

async function bootstrap() {
  await enableMocks();

  ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
    <React.StrictMode>
      <ConfigProvider
        theme={{
          algorithm: theme.defaultAlgorithm,
          token: {
            colorPrimary: "#0f766e",
            colorInfo: "#0f766e",
            colorSuccess: "#15803d",
            colorWarning: "#d97706",
            colorError: "#be123c",
            borderRadius: 18,
            fontFamily: '"Space Grotesk", "Segoe UI", sans-serif',
          },
        }}
      >
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
