/** Jarvis' body: what gestures do and how he greets you. */
import { api } from "../api/client";
import { store } from "../state/store";
import { narrate } from "./narrate";
import { stopSpeaking } from "./speech";
import type { Gesture } from "./vision";

// Order when swiping
export const SWIPE_VIEWS = ["dashboard", "world", "markets", "kpis", "brain"];
const GREET_KEY = "sentinel.lastGreeting";
const GREET_EVERY_MS = 2 * 3600 * 1000;
const AWAY_BEFORE_GREETING_MS = 10 * 60 * 1000;

const HINTS: Record<Gesture, string> = {
  swipe_left: "← Previous view",
  swipe_right: "Next view →",
  open_palm: "✋ Stop",
  thumb_up: "👍 Yes",
  fist: "✊ Back to Jarvis",
};

export function nextView(current: string, gesture: "swipe_left" | "swipe_right"): string {
  const index = Math.max(0, SWIPE_VIEWS.indexOf(current));
  const step = gesture === "swipe_right" ? 1 : -1;
  return SWIPE_VIEWS[(index + step + SWIPE_VIEWS.length) % SWIPE_VIEWS.length] ?? "dashboard";
}

export function handleGesture(gesture: Gesture): void {
  store.set({ gestureHint: { text: HINTS[gesture], at: Date.now() } });
  if (gesture === "swipe_left" || gesture === "swipe_right") {
    store.set((s) => ({ view: nextView(s.view, gesture), viewTab: null }));
  } else if (gesture === "open_palm") {
    stopSpeaking();
  } else if (gesture === "fist") {
    store.set({ view: "dashboard", viewTab: null });
  } else if (gesture === "thumb_up") {
    // Only confirmations in conversation (e.g. "Forget …?") – approvals still need a click
    void api
      .post<{ text: string | null }>("/voice/confirm")
      .then((r) => (r.text ? narrate(r.text) : undefined))
      .catch(() => undefined);
  }
}

function lastGreeting(): number {
  try {
    return Number(localStorage.getItem(GREET_KEY) ?? 0);
  } catch {
    return 0;
  }
}

let awaySince = Date.now() - AWAY_BEFORE_GREETING_MS;

/** Called on every change of presence. */
export function presenceChanged(present: boolean, userName: string, quiet = false): void {
  const now = Date.now();
  if (!present) {
    awaySince = now;
    return;
  }
  // Quiet hours: don't greet
  if (quiet || now - awaySince < AWAY_BEFORE_GREETING_MS) return;
  if (now - lastGreeting() < GREET_EVERY_MS) return;
  try {
    localStorage.setItem(GREET_KEY, String(now));
  } catch {
    // without storage he simply greets more often
  }
  const hour = new Date().getHours();
  const time = hour < 11 ? "Good morning" : hour < 18 ? "Welcome back" : "Good evening";
  const name = Math.random() < 0.5 ? "Sir" : userName || "Sir";
  void narrate(`${time}, ${name}.`);
}
