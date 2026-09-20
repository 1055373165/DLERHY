import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "./app/App";
import { applyTheme, loadTheme } from "./lib/theme";
import "./styles/tokens.css";
import "./styles/global.css";

// Before the first paint, so a dark-mode reader never gets a white flash.
applyTheme(loadTheme());

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
);
