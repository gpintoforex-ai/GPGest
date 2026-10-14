import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import Platform from "./Platform";
import "./style.css";
import "./design-system.css";
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {/^\/plataforma\/?$/.test(window.location.pathname) ? (
      <Platform />
    ) : (
      <App />
    )}
  </React.StrictMode>,
);
