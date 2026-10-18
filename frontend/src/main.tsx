import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import Platform from "./Platform";
import "./style.css";
import "./design-system.css";
import { FeedbackHost } from "./components/InteractionUI";
import "./components/InteractionUI.css";
import "./themes.css";
import "./contrast.css";
import { initializeTheme } from "./ThemeSettings";
initializeTheme();
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <FeedbackHost />
    {/^\/plataforma\/?$/.test(window.location.pathname) ? (
      <Platform />
    ) : (
      <App />
    )}
  </React.StrictMode>,
);
