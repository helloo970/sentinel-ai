/**
 * Camera for Jarvis' body: face (presence, gaze direction) and hand gestures.
 * Runs fully locally with MediaPipe; images are never stored or sent anywhere.
 */
import { store } from "../state/store";
import { reportError } from "./report";

export type Gesture = "swipe_left" | "swipe_right" | "open_palm" | "thumb_up" | "fist";

/** Where the face is in the frame (-1 … 1), for the hologram's gaze. */
export const gaze = { x: 0, y: 0, seen: 0 };

const FRAME_MS = 66;
const HOLD_MS = 450;
const COOLDOWN_MS = 1500;
const SWIPE_DISTANCE = 0.28;
const SWIPE_WINDOW_MS = 450;
const ABSENT_MS = 4000;

const NAMED: Record<string, Gesture> = {
  Open_Palm: "open_palm",
  Thumb_Up: "thumb_up",
  Closed_Fist: "fist",
};

let running = false;
let video: HTMLVideoElement | null = null;
let stream: MediaStream | null = null;
let worker: Worker | null = null;
let busy = false;
let timer = 0;
let listener: ((gesture: Gesture) => void) | null = null;

export function onGesture(fn: (gesture: Gesture) => void): void {
  listener = fn;
}

/** Pure gesture logic (testable): held gestures and swipes. */
export class GestureTracker {
  private held: { name: string; since: number } | null = null;
  private trail: { t: number; x: number }[] = [];
  private lastFired = Number.NEGATIVE_INFINITY;

  update(now: number, name: string | null, wristX: number | null): Gesture | null {
    if (now - this.lastFired < COOLDOWN_MS) return null;
    if (wristX !== null) {
      this.trail.push({ t: now, x: wristX });
      this.trail = this.trail.filter((p) => now - p.t <= SWIPE_WINDOW_MS);
      const first = this.trail[0];
      if (first && this.trail.length >= 3) {
        const dx = wristX - first.x;
        // The camera image is mirrored: hand to the right = x gets smaller
        if (Math.abs(dx) >= SWIPE_DISTANCE)
          return this.fire(now, dx < 0 ? "swipe_right" : "swipe_left");
      }
    } else {
      this.trail = [];
    }
    const gesture = name ? NAMED[name] : undefined;
    if (!gesture) {
      this.held = null;
      return null;
    }
    if (!this.held || this.held.name !== name) {
      this.held = { name: name ?? "", since: now };
      return null;
    }
    return now - this.held.since >= HOLD_MS ? this.fire(now, gesture) : null;
  }

  private fire(now: number, gesture: Gesture): Gesture {
    this.lastFired = now;
    this.held = null;
    this.trail = [];
    return gesture;
  }
}

const tracker = new GestureTracker();

export async function startVision(): Promise<void> {
  if (running) return;
  running = true;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { width: 640, height: 480, facingMode: "user" },
      audio: false,
    });
    if (!running) {
      stopVision();
      return;
    }
    video = document.createElement("video");
    video.muted = true;
    video.playsInline = true;
    video.srcObject = stream;
    await video.play();
    // MediaPipe in its own thread (see vision.worker.ts)
    worker = new Worker(new URL("./vision.worker.ts", import.meta.url));
    await new Promise<void>((resolve, reject) => {
      if (!worker) return reject(new Error("worker missing"));
      worker.onmessage = (event: MessageEvent) => {
        const data = event.data as { type: string; message?: string };
        if (data.type === "ready") resolve();
        else if (data.type === "error") reject(new Error(data.message));
      };
      worker.postMessage({ type: "init" });
    });
    worker.onmessage = (event: MessageEvent) => onResult(event.data);
    store.set({ camera: { active: true, present: false, error: null } });
    loop();
  } catch (error) {
    reportError("camera", error);
    running = false;
    stopVision();
    store.set({ camera: { active: false, present: false, error: String(error) } });
  }
}

function onResult(data: {
  type: string;
  nose?: { x: number; y: number } | null;
  gesture?: string | null;
  wrist?: number | null;
  message?: string;
}): void {
  busy = false;
  if (data.type === "error") {
    reportError("camera-frame", data.message);
    return;
  }
  if (data.type !== "result") return;
  const now = performance.now();
  if (data.nose) {
    gaze.x = (0.5 - data.nose.x) * 2;
    gaze.y = (0.5 - data.nose.y) * 2;
    gaze.seen = now;
  }
  const present = now - gaze.seen < ABSENT_MS;
  if (store.get().camera.present !== present) {
    store.set((s) => ({ camera: { ...s.camera, present } }));
  }
  const gesture = tracker.update(now, data.gesture ?? null, data.wrist ?? null);
  if (gesture) listener?.(gesture);
}

function loop(): void {
  if (!running || !video || !worker) return;
  timer = window.setTimeout(loop, FRAME_MS);
  if (busy || document.hidden || video.readyState < 2) return;
  busy = true;
  createImageBitmap(video)
    .then((bitmap) =>
      worker?.postMessage({ type: "frame", bitmap, ts: performance.now() }, [bitmap]),
    )
    .catch(() => {
      busy = false;
    });
}

export function stopVision(): void {
  running = false;
  busy = false;
  window.clearTimeout(timer);
  stream?.getTracks().forEach((t) => t.stop());
  stream = null;
  video = null;
  worker?.terminate();
  worker = null;
  store.set((s) => ({ camera: { ...s.camera, active: false, present: false } }));
}
