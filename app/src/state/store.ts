import { useSyncExternalStore } from "react";
import type { Approval, Severity, Status } from "../api/types";
import { loadPrefs } from "../lib/prefs";

export interface Toast {
  id: number;
  title: string;
  message: string;
  severity: Severity;
  undoId?: string | null;
  expires?: number | null;
  created: number;
}

export interface AppState {
  connected: boolean;
  status: Status | null;
  approvals: Approval[];
  toasts: Toast[];
  voiceState: string;
  alertsVersion: number;
  reportsVersion: number;
  voiceChat: { text: string; done: boolean } | null;
  approvalsOpen: boolean;
  pinRequest: { title: string } | null;
  view: string;
  viewTab: string | null;
  /** Last view Jarvis opened by voice, to tell it apart from manual navigation */
  voiceView: { view: string; at: number } | null;
  hacker: { id: number; title: string; lines: string[] } | null;
  tourRequest: number;
  tourMorning: boolean;
  brainVersion: number;
  brainActive: number[];
  brainFocus: { id: number; forget: boolean; at: number } | null;
  layout: "voice" | "menu";
  camera: { active: boolean; present: boolean; error: string | null };
  gestureHint: { text: string; at: number } | null;
  /** Time of the last successful action – the hologram briefly looks happy */
  happyAt: number;
  cameraEnabled: boolean;
}

type Listener = () => void;

export function createStore<T>(initial: T) {
  let state = initial;
  const listeners = new Set<Listener>();
  return {
    get: () => state,
    set(update: Partial<T> | ((s: T) => Partial<T>)) {
      const patch = typeof update === "function" ? update(state) : update;
      state = { ...state, ...patch };
      listeners.forEach((l) => l());
    },
    subscribe(listener: Listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

export const store = createStore<AppState>({
  connected: false,
  status: null,
  approvals: [],
  toasts: [],
  voiceState: "off",
  alertsVersion: 0,
  reportsVersion: 0,
  voiceChat: null,
  approvalsOpen: false,
  pinRequest: null,
  view: "dashboard",
  viewTab: null,
  voiceView: null,
  hacker: null,
  tourRequest: 0,
  tourMorning: false,
  brainVersion: 0,
  brainActive: [],
  brainFocus: null,
  layout: loadPrefs().layout,
  camera: { active: false, present: false, error: null },
  gestureHint: null,
  happyAt: 0,
  cameraEnabled: loadPrefs().camera,
});

export function useStore<R>(selector: (s: AppState) => R): R {
  return useSyncExternalStore(store.subscribe, () => selector(store.get()));
}

let toastId = 0;
const MAX_TOASTS = 5;

export function pushToast(toast: Omit<Toast, "id" | "created">, ttlMs = 7000): number {
  const id = ++toastId;
  const entry: Toast = { ...toast, id, created: Date.now() };
  store.set((s) => ({ toasts: [...s.toasts, entry].slice(-MAX_TOASTS) }));
  const lifetime = toast.expires ? Math.max(ttlMs, toast.expires - Date.now()) : ttlMs;
  setTimeout(() => dismissToast(id), lifetime);
  return id;
}

export function dismissToast(id: number): void {
  store.set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
}
