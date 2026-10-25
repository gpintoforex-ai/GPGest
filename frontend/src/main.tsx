import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import Platform from "./Platform";
import LegalPage, { LegalFooter } from "./Legal";
import "./style.css";
import "./design-system.css";
import { FeedbackHost } from "./components/InteractionUI";
import "./components/InteractionUI.css";
import "./themes.css";
import "./contrast.css";
import "./filters.css";
import "./page-cards.css";
import "./action-colours.css";
import "./payment-identities.css";
import "./toggle-controls.css";
import "./confirmation-windows.css";
import "./brand-navigation.css";
import "./menu-current.css";
import { initializeTheme } from "./ThemeSettings";
import { ensureBrowserUUID } from "./lib/browserCrypto";
ensureBrowserUUID();
initializeTheme();
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <FeedbackHost />
    {window.location.pathname.startsWith("/legal/") ? <LegalPage /> : /^\/plataforma\/?$/.test(window.location.pathname) ? (
      <Platform />
    ) : (
      <App />
    )}
    <LegalFooter />
  </React.StrictMode>,
);
