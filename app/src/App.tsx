import type { Status } from "./api/types";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { isTauri, loadTauriConfig, setConfig, waitForBackend } from "./api/client";
import { ApprovalsDrawer } from "./components/Approvals";
import { AutonomyControl } from "./components/AutonomyControl";
import { HackerOverlay } from "./components/HackerOverlay";
import { HudCore } from "./components/HudCore";
import { Icon } from "./components/Icon";
import { KillSwitch, triggerHalt } from "./components/KillSwitch";
import { Chip } from "./components/Panel";
import { PinDialog } from "./components/PinDialog";
import { Toasts } from "./components/Toasts";
import { useBody } from "./hooks/useBody";
import { useEvents } from "./hooks/useEvents";
import { store, useStore } from "./state/store";
import { Agents } from "./views/Agents";
import { Audit } from "./views/Audit";
import { Automation } from "./views/Automation";
import { Dashboard } from "./views/Dashboard";
import { Jarvis } from "./views/Jarvis";
import { Memory } from "./views/Memory";
import { Network } from "./views/Network";
import { Security } from "./views/Security";
import { Settings } from "./views/Settings";
import { Shop } from "./views/Shop";
import { BrainStage } from "./views/BrainStage";
import { Kpis } from "./views/Kpis";
import { Markets } from "./views/Markets";
import { World } from "./views/World";

// The start screen is Jarvis. Jarvis opens every other section full screen by voice
// (or Ctrl+K as a fallback). There is no sidebar.
const VIEWS = {
  dashboard: Jarvis,
  jarvis: Jarvis,
  brain: BrainStage,
  world: World,
  markets: Markets,
  kpis: Kpis,
  overview: Dashboard,
  security: Security,
  network: Network,
  automation: Automation,
  shop: Shop,
  agents: Agents,
  memory: Memory,
  audit: Audit,
  settings: Settings,
} as const;
type ViewName = keyof typeof VIEWS;
// "With menu" layout: the dashboard is the overview, Jarvis has its own tab
const MAIN: ViewName[] = [
  "dashboard",
  "jarvis",
  "world",
  "markets",
  "kpis",
  "brain",
  "security",
  "network",
  "settings",
];
const MORE: ViewName[] = ["automation", "shop", "agents", "memory", "audit"];
const PALETTE: ViewName[] = [
  "brain",
  "world",
  "markets",
  "kpis",
  "security",
  "network",
  "automation",
  "shop",
  "agents",
  "memory",
  "audit",
  "overview",
  "settings",
];

const EMPTY_WARNINGS: NonNullable<Status["warnings"]> = [];

function goHome() {
  store.set({ view: "dashboard", viewTab: null });
}

function Shell() {
  const { t } = useTranslation();
  const stored = useStore((s) => s.view);
  const layout = useStore((s) => s.layout);
  const menu = layout === "menu";
  const view: ViewName =
    stored in VIEWS && (menu || stored !== "jarvis") ? (stored as ViewName) : "dashboard";
  const [moreOpen, setMoreOpen] = useState(() => MORE.includes(view));
  const [palette, setPalette] = useState(false);
  const status = useStore((s) => s.status);
  const connected = useStore((s) => s.connected);
  const approvals = useStore((s) => s.approvals.length);
  useEvents();
  useBody();
  const camera = useStore((s) => s.camera);
  const hint = useStore((s) => s.gestureHint);
  const warnings = useStore((s) => s.status?.warnings ?? EMPTY_WARNINGS);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      // Emergency stop by keyboard: Ctrl+Alt+End
      if (event.ctrlKey && event.altKey && event.key === "End") void triggerHalt("hotkey");
      else if (event.ctrlKey && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPalette((open) => !open);
      } else if (event.key === "Escape") {
        setPalette(false);
        if (store.get().view !== "dashboard") goHome();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const View = menu && view === "dashboard" ? Dashboard : VIEWS[view];
  const home = view === "dashboard" || (menu && view === "jarvis");
  const alertCount = (status?.alerts.critical ?? 0) + (status?.alerts.warn ?? 0);

  return (
    <div className={`shell ${menu ? "" : "no-nav"}`}>
      <header className="topbar">
        <button className="brand brand-btn" onClick={goHome} title={t("stage.home")}>
          <div style={{ width: 34 }}>
            <HudCore state={status?.halted ? "halted" : "idle"} label={false} />
          </div>
          <div>
            <b>SENTINEL</b>
            <small>{t("brand.tagline")}</small>
          </div>
        </button>
        <div className="row grow">
          <Chip tone={connected ? "ok" : "crit"} dot>
            {connected ? t("status.connected") : t("status.offline")}
          </Chip>
          {status && !status.llm.available && <Chip tone="muted">{t("status.llmOffline")}</Chip>}
          {camera.active && (
            <Chip tone={camera.present ? "ok" : "muted"} dot>
              ● {t("camera.on")}
            </Chip>
          )}
          {camera.error && <Chip tone="warn">{t("camera.error")}</Chip>}
          {warnings.length > 0 && (
            <button
              className="btn small ghost warn"
              onClick={() => store.set({ view: "settings", viewTab: null })}
              title={warnings.map((w) => w.title).join(", ")}
            >
              ⚠ {t("setup.chip", { count: warnings.length })}
            </button>
          )}
          <button
            className="btn small ghost"
            onClick={() => store.set((s) => ({ approvalsOpen: !s.approvalsOpen }))}
          >
            <Icon name="bell" size={14} /> {t("status.approvals")}{" "}
            {approvals > 0 && <span className="warn">({approvals})</span>}
          </button>
        </div>
        <button
          className="btn small ghost"
          onClick={() => store.set({ view: "settings", viewTab: null })}
          title={t("nav.settings")}
          aria-label={t("nav.settings")}
        >
          ⚙ {t("nav.settings")}
        </button>
        <AutonomyControl compact />
        <KillSwitch />
      </header>
      {menu && (
        <nav className="nav" aria-label="Navigation">
          {MAIN.map((name) => (
            <button
              key={name}
              className="nav-btn"
              aria-current={view === name ? "page" : undefined}
              onClick={() => store.set({ view: name, viewTab: null })}
            >
              <Icon name={name} />
              {t(`nav.${name}`)}
              {name === "security" && alertCount > 0 && (
                <span className="nav-badge">{alertCount}</span>
              )}
            </button>
          ))}
          <button
            className="nav-btn nav-more"
            aria-expanded={moreOpen}
            onClick={() => setMoreOpen((open) => !open)}
          >
            <span className="nav-more-icon">{moreOpen ? "−" : "+"}</span>
            {t("nav.more")}
          </button>
          {moreOpen &&
            MORE.map((name) => (
              <button
                key={name}
                className="nav-btn nav-sub"
                aria-current={view === name ? "page" : undefined}
                onClick={() => store.set({ view: name, viewTab: null })}
              >
                <Icon name={name} />
                {t(`nav.${name}`)}
              </button>
            ))}
          <div className="nav-foot">
            {status?.user_name ?? ""} · v{status?.version ?? "–"}
          </div>
        </nav>
      )}
      <main className={`main ${home ? "home" : "stage"}`}>
        {status?.halted && (
          <div className="halt-banner" style={{ marginBottom: 16 }}>
            {t("killswitch.banner")}
          </div>
        )}
        {!home && !menu && (
          <button className="btn small ghost stage-close" onClick={goHome}>
            ← {t("stage.back")}
          </button>
        )}
        <View />
      </main>
      {palette && (
        <div className="palette" role="dialog" onClick={() => setPalette(false)}>
          <div className="palette-box" onClick={(e) => e.stopPropagation()}>
            <div className="faint">{t("stage.palette")}</div>
            {PALETTE.map((name) => (
              <button
                key={name}
                className="nav-btn"
                onClick={() => {
                  store.set({ view: name, viewTab: null });
                  setPalette(false);
                }}
              >
                <Icon name={name} />
                {t(`nav.${name}`)}
              </button>
            ))}
          </div>
        </div>
      )}
      {hint && Date.now() - hint.at < 1600 && (
        <div className="gesture-hint" key={hint.at}>
          {hint.text}
        </div>
      )}
      <ApprovalsDrawer />
      <Toasts />
      <HackerOverlay />
      <PinDialog />
    </div>
  );
}

function ConnectScreen({ onConnect }: { onConnect: () => void }) {
  const [port, setPort] = useState("8765");
  const [token, setToken] = useState("");
  return (
    <div className="boot">
      <form
        className="stack"
        style={{ width: 360 }}
        onSubmit={async (e) => {
          e.preventDefault();
          setConfig({ baseUrl: `http://127.0.0.1:${port}`, token: token.trim() });
          if (await waitForBackend(3000)) onConnect();
          else window.alert("No connection – check port/token.");
        }}
      >
        <h1>SENTINEL · Development</h1>
        <p className="dim">Browser mode: the token is in %LOCALAPPDATA%\SentinelAI\session.json.</p>
        <input
          className="input"
          value={port}
          onChange={(e) => setPort(e.target.value)}
          placeholder="Port"
        />
        <input
          className="input"
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="Token"
        />
        <button className="btn primary">Connect</button>
      </form>
    </div>
  );
}

export function App() {
  const [phase, setPhase] = useState<"boot" | "connect" | "ready" | "error">(
    isTauri() ? "boot" : "connect",
  );

  useEffect(() => {
    if (!isTauri()) return;
    (async () => {
      try {
        setConfig(await loadTauriConfig());
        setPhase((await waitForBackend(45000)) ? "ready" : "error");
      } catch {
        setPhase("error");
      }
    })();
  }, []);

  if (phase === "ready") return <Shell />;
  if (phase === "connect") return <ConnectScreen onConnect={() => setPhase("ready")} />;
  return (
    <div className="boot">
      <div className="stack" style={{ alignItems: "center" }}>
        <HudCore state={phase === "error" ? "critical" : "thinking"} label={false} />
        <h1>{phase === "error" ? "Backend not reachable" : "Systems starting up …"}</h1>
        {phase === "error" && <p className="dim">Log: %LOCALAPPDATA%\SentinelAI\backend.log</p>}
      </div>
    </div>
  );
}
