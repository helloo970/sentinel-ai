import { useEffect } from "react";
import { api, isTauri, stream } from "../api/client";
import type { Approval, BackendEvent, Severity, Status } from "../api/types";
import { loadPrefs } from "../lib/prefs";
import { reportError } from "../lib/report";
import { playSiren } from "../lib/siren";
import { speak } from "../lib/speech";
import { pushToast, store } from "../state/store";

export async function refreshStatus(): Promise<void> {
  try {
    const status = await api.get<Status>("/status");
    store.set({
      status,
      connected: true,
      voiceState: status.voice.state === "off" ? store.get().voiceState : status.voice.state,
    });
  } catch {
    store.set({ connected: false });
  }
}

export async function refreshApprovals(): Promise<void> {
  try {
    store.set({ approvals: await api.get<Approval[]>("/approvals") });
  } catch {
    // The connection is shown via the status
  }
}

async function osNotify(title: string, body: string): Promise<void> {
  if (!isTauri()) return;
  try {
    const notification = await import("@tauri-apps/plugin-notification");
    let granted = await notification.isPermissionGranted();
    if (!granted) granted = (await notification.requestPermission()) === "granted";
    if (granted) notification.sendNotification({ title, body });
  } catch {
    // Notifications not available
  }
}

async function showWindow(): Promise<void> {
  if (!isTauri()) return;
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    await invoke("show_main_window");
  } catch {
    // Window stays as it is
  }
}

const str = (value: unknown) => (typeof value === "string" ? value : "");

export function handleEvent(event: BackendEvent): void {
  const data = event.data;
  switch (event.kind) {
    case "approval":
      void refreshApprovals();
      void refreshStatus();
      pushToast({ title: "Approval needed", message: str(data.preview), severity: "warn" }, 10000);
      if (document.hidden) void osNotify("SENTINEL – approval needed", str(data.preview));
      break;
    case "action":
      void refreshStatus();
      store.set({ happyAt: Date.now() });
      pushToast(
        {
          title: `${str(data.title)}${data.autonomous ? " · autonomous" : ""}`,
          message: str(data.message),
          severity: "info",
          undoId: (data.undo_id as string | null) ?? null,
          expires: (data.undo_expires as number | null) ?? null,
        },
        8000,
      );
      break;
    case "notification": {
      const severity = (str(data.severity) || "info") as Severity;
      pushToast({ title: str(data.title), message: str(data.message), severity }, 9000);
      if (document.hidden || severity === "critical") {
        void osNotify(str(data.title), str(data.message));
      }
      break;
    }
    case "alert":
      store.set((s) => ({ alertsVersion: s.alertsVersion + 1 }));
      if ((data.severity === "critical" || data.severity === "warn") && loadPrefs().alarmSound) {
        playSiren(data.severity);
      }
      void refreshStatus();
      break;
    case "halt":
    case "autonomy":
      void refreshStatus();
      if (event.kind === "autonomy" && data.reason) {
        pushToast({ title: "Autonomy", message: str(data.reason), severity: "warn" });
      }
      break;
    case "warning":
      pushToast({ title: str(data.title), message: str(data.message), severity: "warn" }, 12000);
      break;
    case "undo":
      pushToast({ title: "Undo", message: str(data.message), severity: "info" });
      break;
    case "voice":
      store.set({ voiceState: str(data.state) || "idle" });
      break;
    case "say":
      speak(str(data.text));
      break;
    case "transcript":
      store.set({ voiceChat: { text: `› ${str(data.text)}\n`, done: false } });
      break;
    case "chat":
      if (data.type === "text") {
        store.set((s) => ({
          voiceChat: { text: (s.voiceChat?.text ?? "") + str(data.text), done: false },
        }));
      } else if (data.type === "done") {
        store.set((s) => ({ voiceChat: s.voiceChat && { ...s.voiceChat, done: true } }));
      }
      break;
    case "navigate": {
      const title = str(data.title) || str(data.view);
      store.set({
        view: str(data.view),
        viewTab: data.tab ? str(data.tab) : null,
        hacker: { id: Date.now(), title, lines: hackerLines(title) },
      });
      break;
    }
    case "wake":
      void showWindow();
      store.set({ view: "world", tourRequest: Date.now(), tourMorning: Boolean(data.morning) });
      break;
    case "open_web":
      void openWeb(str(data.url), str(data.title));
      break;
    case "brain_changed":
      store.set((s) => ({ brainVersion: s.brainVersion + 1 }));
      break;
    case "brain_active": {
      const ids = Array.isArray(data.ids) ? data.ids.map(Number) : [];
      store.set({ brainActive: ids });
      // The glow fades again after a while
      window.setTimeout(() => {
        if (store.get().brainActive === ids) store.set({ brainActive: [] });
      }, 12000);
      break;
    }
    case "brain_focus":
      store.set({
        view: "brain",
        brainFocus: { id: Number(data.id), forget: Boolean(data.forget), at: Date.now() },
      });
      break;
    case "brain_show":
      store.set({ view: "brain", brainFocus: null });
      break;
    case "agent_report":
      store.set((s) => ({ reportsVersion: s.reportsVersion + 1 }));
      pushToast({ title: "Agent report", message: str(data.title), severity: "info" });
      break;
    default:
      break;
  }
}

function hackerLines(title: string): string[] {
  return [
    `> jarvis --open "${title}"`,
    "> connecting to SENTINEL core ... ok",
    "> loading modules ... ok",
    "> checking permissions ... ok",
    `> ${title.toUpperCase()} online`,
  ];
}

/** Persistent event stream with automatic reconnect. */
export function startEventStream(): () => void {
  const controller = new AbortController();
  let delay = 1000;
  (async () => {
    while (!controller.signal.aborted) {
      try {
        for await (const event of stream<BackendEvent>("/events", { signal: controller.signal })) {
          delay = 1000;
          store.set({ connected: true });
          handleEvent(event);
        }
      } catch {
        if (controller.signal.aborted) return;
        store.set({ connected: false });
      }
      await new Promise((resolve) => setTimeout(resolve, delay));
      delay = Math.min(delay * 2, 15000);
      void refreshStatus();
    }
  })();
  return () => controller.abort();
}

// Write unexpected UI errors to the backend log
if (typeof window !== "undefined") {
  window.addEventListener("error", (e) =>
    // Send the location too: script load errors often have no stack
    reportError(`window ${e.filename || "?"}:${e.lineno}:${e.colno}`, e.error ?? e.message),
  );
  window.addEventListener("unhandledrejection", (e) => reportError("promise", e.reason));
}

export function useEvents(): void {
  useEffect(() => {
    void refreshStatus();
    void refreshApprovals();
    const stop = startEventStream();
    const timer = setInterval(() => void refreshStatus(), 15000);
    return () => {
      stop();
      clearInterval(timer);
    };
  }, []);
}

/** Website in its own app window (without access to SENTINEL), otherwise a new tab. */
async function openWeb(url: string, title: string) {
  if (!/^https:\/\//.test(url)) return;
  if (!isTauri()) {
    window.open(url, "_blank", "noopener");
    return;
  }
  try {
    const { WebviewWindow } = await import("@tauri-apps/api/webviewWindow");
    new WebviewWindow(`web-${Date.now()}`, {
      url,
      title: `SENTINEL · ${title || url}`,
      width: 1280,
      height: 820,
      center: true,
      focus: true,
    });
  } catch {
    window.open(url, "_blank", "noopener");
  }
}
