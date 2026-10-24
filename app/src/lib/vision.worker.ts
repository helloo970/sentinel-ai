/// <reference lib="webworker" />
/**
 * MediaPipe runs here in its own thread: the main window has frozen prototypes
 * (freezePrototype as protection), which MediaPipe can't cope with.
 */
import { FaceLandmarker, FilesetResolver, GestureRecognizer } from "@mediapipe/tasks-vision";

let face: FaceLandmarker | null = null;
let hands: GestureRecognizer | null = null;

async function init(): Promise<void> {
  // Classic worker: MediaPipe loads its WASM runtime via importScripts
  const files = await FilesetResolver.forVisionTasks("/vision/wasm");
  const create = async (delegate: "GPU" | "CPU") => {
    face = await FaceLandmarker.createFromOptions(files, {
      baseOptions: { modelAssetPath: "/vision/face_landmarker.task", delegate },
      runningMode: "VIDEO",
      numFaces: 1,
    });
    hands = await GestureRecognizer.createFromOptions(files, {
      baseOptions: { modelAssetPath: "/vision/gesture_recognizer.task", delegate },
      runningMode: "VIDEO",
      numHands: 1,
    });
  };
  try {
    await create("GPU");
  } catch {
    await create("CPU"); // without GPU acceleration
  }
}

self.onmessage = async (event: MessageEvent) => {
  const msg = event.data as { type: string; bitmap?: ImageBitmap; ts?: number };
  if (msg.type === "init") {
    try {
      await init();
      self.postMessage({ type: "ready" });
    } catch (error) {
      self.postMessage({ type: "error", message: String(error) });
    }
    return;
  }
  if (msg.type !== "frame" || !msg.bitmap || !face || !hands) {
    msg.bitmap?.close();
    return;
  }
  const ts = msg.ts ?? performance.now();
  try {
    const nose = face.detectForVideo(msg.bitmap, ts).faceLandmarks[0]?.[1] ?? null;
    const result = hands.recognizeForVideo(msg.bitmap, ts);
    self.postMessage({
      type: "result",
      nose: nose ? { x: nose.x, y: nose.y } : null,
      gesture: result.gestures[0]?.[0]?.categoryName ?? null,
      wrist: result.landmarks[0]?.[0]?.x ?? null,
    });
  } catch (error) {
    self.postMessage({ type: "error", message: String(error) });
  } finally {
    msg.bitmap.close();
  }
};
