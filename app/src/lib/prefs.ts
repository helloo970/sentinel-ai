/** Local UI settings (no secrets). */
export interface Prefs {
  motion: boolean;
  speakReplies: boolean;
  alarmSound: boolean;
  liveNewsAfterTour: boolean;
  /** voice = Jarvis only (voice), menu = dashboard and tabs on the left */
  layout: "voice" | "menu";
  /** Camera for gaze, greeting and gestures (runs locally only) */
  camera: boolean;
}

const KEY = "sentinel.prefs";
const DEFAULTS: Prefs = {
  motion: true,
  speakReplies: false,
  alarmSound: true,
  liveNewsAfterTour: true,
  layout: "voice",
  camera: true,
};

export function loadPrefs(): Prefs {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? { ...DEFAULTS, ...(JSON.parse(raw) as Partial<Prefs>) } : DEFAULTS;
  } catch {
    return DEFAULTS;
  }
}

export function savePrefs(prefs: Prefs): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(prefs));
  } catch {
    // storage not available
  }
  applyPrefs(prefs);
}

export function applyPrefs(prefs: Prefs): void {
  // On: the system default (prefers-reduced-motion) still applies. Off: stop all motion.
  if (prefs.motion) delete document.documentElement.dataset.motion;
  else document.documentElement.dataset.motion = "off";
  document.documentElement.lang = "en";
}
