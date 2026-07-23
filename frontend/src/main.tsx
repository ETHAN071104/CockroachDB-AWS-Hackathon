import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "./App";
import { GuestSessionProvider } from "./guest/GuestSessionProvider";
import "./styles/index.css";

const root = document.getElementById("root");

if (!root) {
  throw new Error("Application root element is missing.");
}

createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <GuestSessionProvider>
        <App />
      </GuestSessionProvider>
    </BrowserRouter>
  </StrictMode>,
);
