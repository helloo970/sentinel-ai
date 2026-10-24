// Puts the MediaPipe files for camera, face and gestures into public/vision
// (WASM from the npm package, models downloaded once from Google's official storage).
import { Buffer } from "node:buffer";
import { copyFileSync, existsSync, mkdirSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const out = join(root, "public", "vision");
const wasmSrc = join(root, "node_modules", "@mediapipe", "tasks-vision", "wasm");
const MODELS = {
  "face_landmarker.task":
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
  "gesture_recognizer.task":
    "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task",
};

mkdirSync(join(out, "wasm"), { recursive: true });
// Classic worker with SIMD (WebView2) – the other variants aren't needed
for (const name of readdirSync(wasmSrc).filter((n) => n.startsWith("vision_wasm_internal")))
  copyFileSync(join(wasmSrc, name), join(out, "wasm", name));

for (const [name, url] of Object.entries(MODELS)) {
  const target = join(out, name);
  if (existsSync(target) && statSync(target).size > 100_000) continue;
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${name}: HTTP ${response.status}`);
  writeFileSync(target, Buffer.from(await response.arrayBuffer()));
  console.log(`vision: ${name} downloaded`);
}
console.log("vision: ready");
