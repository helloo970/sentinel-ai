import "@fontsource/orbitron/500.css";
import "@fontsource/orbitron/700.css";
import "@fontsource/rajdhani/400.css";
import "@fontsource/rajdhani/500.css";
import "@fontsource/rajdhani/600.css";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/hud.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { initI18n } from "./i18n";
import { applyPrefs, loadPrefs } from "./lib/prefs";
import { unlockSiren } from "./lib/siren";

const prefs = loadPrefs();
applyPrefs(prefs);
initI18n();
// Unlock audio: browsers only allow sound after the first user action
window.addEventListener("pointerdown", unlockSiren, { once: true });

createRoot(document.getElementById("root") as HTMLElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
