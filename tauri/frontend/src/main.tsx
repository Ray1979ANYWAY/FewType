import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import HudOverlay from "./components/HudOverlay";
import { I18nProvider } from "./i18n";
import "./index.css";

// 悬浮状态条窗口：url = /#hud（独立置顶小窗），主界面走 /#/ 普通路由
const isHud = window.location.hash.startsWith("#hud");

// HUD 窗口需要 webview 页面透明（胶囊之外透出桌面），主窗口保持暗底
if (isHud) {
  document.documentElement.classList.add("hud-window");
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <I18nProvider>{isHud ? <HudOverlay /> : <App />}</I18nProvider>
  </React.StrictMode>
);
