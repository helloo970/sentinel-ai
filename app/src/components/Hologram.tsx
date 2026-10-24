import { useEffect, useRef, useState } from "react";
import type { BufferAttribute, Group, Material, Sprite } from "three";
import type { CoreState } from "../lib/coreState";
import faceMesh from "../lib/faceMesh.json";
import { speechLevel } from "../lib/speech";
import { gaze } from "../lib/vision";
import { reportError } from "../lib/report";
import { store } from "../state/store";
import { HudCore } from "./HudCore";

const SCALE = 10;
// MediaPipe indices: lower lip, mouth corners
const LOWER_LIP = 14;
const CORNERS = [61, 291];

export const EMOTION_COLORS: Record<string, string> = {
  idle: "#2f8fff",
  listening: "#4fb0ff",
  thinking: "#3d9bff",
  speaking: "#48a6ff",
  alert: "#8fd3ff",
  critical: "#ff2d4d",
  halted: "#8a8a8a",
  happy: "#93c5fd",
};

interface Props {
  state: CoreState;
}

/** Iron Man-style hologram: a face made of light points that speaks, looks at you and shows emotions. */
export function Hologram({ state }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const stateRef = useRef(state);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    let disposed = false;
    let frame = 0;
    let observer: ResizeObserver | null = null;
    let cleanup: (() => void) | null = null;

    void (async () => {
      try {
        const [THREE, post] = await Promise.all([
          import("three"),
          Promise.all([
            import("three/examples/jsm/postprocessing/EffectComposer.js"),
            import("three/examples/jsm/postprocessing/RenderPass.js"),
            import("three/examples/jsm/postprocessing/UnrealBloomPass.js"),
          ]),
        ]);
        if (disposed) return;
        const [{ EffectComposer }, { RenderPass }, { UnrealBloomPass }] = post;
        // Lines of the MediaPipe face (precomputed, no MediaPipe needed in the main window)
        const edges = (flat: number[]) =>
          Array.from({ length: flat.length / 2 }, (_, i) => ({
            start: flat[i * 2] ?? 0,
            end: flat[i * 2 + 1] ?? 0,
          }));

        const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        renderer.setSize(host.clientWidth, host.clientHeight);
        host.appendChild(renderer.domElement);
        const scene = new THREE.Scene();
        const camera = new THREE.PerspectiveCamera(
          35,
          host.clientWidth / host.clientHeight,
          1,
          2000,
        );
        camera.position.set(0, 0, 330);

        // Base shape: canonical MediaPipe face
        const base = (faceMesh.vertices as number[][]).map(([x = 0, y = 0, z = 0]) => [
          x * SCALE,
          y * SCALE,
          z * SCALE,
        ]);
        const positions = new Float32Array(base.length * 3);
        base.forEach((v, i) => positions.set(v as number[], i * 3));
        const attribute = new THREE.BufferAttribute(positions, 3) as BufferAttribute;
        const lowerLipY = base[LOWER_LIP]?.[1] ?? -40;
        const jaw = base.map(([x = 0, y = 0]) =>
          y < lowerLipY + 2 ? Math.max(0, 1 - Math.abs(x) / 70) : 0,
        );
        const cornerWeights = base.map(([x = 0, y = 0]) =>
          Math.max(
            ...CORNERS.map((c) => {
              const p = base[c] ?? [0, 0];
              const d = Math.hypot(x - (p[0] ?? 0), y - (p[1] ?? 0));
              return Math.max(0, 1 - d / 18);
            }),
          ),
        );

        const head: Group = new THREE.Group();
        scene.add(head);
        const geometry = (pairs: { start: number; end: number }[]) => {
          const g = new THREE.BufferGeometry();
          g.setAttribute("position", attribute);
          g.setIndex(pairs.flatMap((p) => [p.start, p.end]));
          return g;
        };
        const lineMaterial = new THREE.LineBasicMaterial({
          transparent: true,
          opacity: 0.16,
          blending: THREE.AdditiveBlending,
          depthWrite: false,
        });
        const contourMaterial = new THREE.LineBasicMaterial({
          transparent: true,
          opacity: 0.75,
          blending: THREE.AdditiveBlending,
          depthWrite: false,
        });
        const pointGeometry = new THREE.BufferGeometry();
        pointGeometry.setAttribute("position", attribute);
        const pointMaterial = new THREE.PointsMaterial({
          size: 1.6,
          transparent: true,
          opacity: 0.85,
          blending: THREE.AdditiveBlending,
          depthWrite: false,
        });
        head.add(
          new THREE.LineSegments(geometry(edges(faceMesh.tesselation)), lineMaterial),
          new THREE.LineSegments(geometry(edges(faceMesh.contours)), contourMaterial),
          new THREE.Points(pointGeometry, pointMaterial),
        );

        // Glowing eyes
        const glow = (() => {
          const c = document.createElement("canvas");
          c.width = c.height = 64;
          const x = c.getContext("2d");
          if (x) {
            const g = x.createRadialGradient(32, 32, 0, 32, 32, 32);
            g.addColorStop(0, "rgba(255,255,255,1)");
            g.addColorStop(0.35, "rgba(255,255,255,0.5)");
            g.addColorStop(1, "rgba(255,255,255,0)");
            x.fillStyle = g;
            x.fillRect(0, 0, 64, 64);
          }
          return new THREE.CanvasTexture(c);
        })();
        const center = (pairs: { start: number; end: number }[]) => {
          const ids = [...new Set(pairs.flatMap((p) => [p.start, p.end]))];
          let sx = 0;
          let sy = 0;
          let sz = 0;
          for (const i of ids) {
            const v = base[i] ?? [0, 0, 0];
            sx += v[0] ?? 0;
            sy += v[1] ?? 0;
            sz += v[2] ?? 0;
          }
          const n = Math.max(1, ids.length);
          return [sx / n, sy / n, sz / n];
        };
        const eyes: Sprite[] = [edges(faceMesh.leftEye), edges(faceMesh.rightEye)].map((pairs) => {
          const [x = 0, y = 0, z = 0] = center(pairs);
          const sprite = new THREE.Sprite(
            new THREE.SpriteMaterial({
              map: glow,
              transparent: true,
              blending: THREE.AdditiveBlending,
              depthWrite: false,
            }),
          );
          sprite.position.set(x, y, z + 4);
          sprite.scale.set(22, 12, 1);
          head.add(sprite);
          return sprite;
        });

        const composer = new EffectComposer(renderer);
        composer.addPass(new RenderPass(scene, camera));
        composer.addPass(
          new UnrealBloomPass(
            new THREE.Vector2(host.clientWidth, host.clientHeight),
            1.1,
            0.5,
            0.05,
          ),
        );

        const color = new THREE.Color("#2f8fff");
        const target = new THREE.Color();
        let mouth = 0;
        let smile = 0;
        let nextBlink = performance.now() + 3000;
        const tick = (time: number) => {
          const current = stateRef.current;
          const happy = Date.now() - store.get().happyAt < 2500;
          const mood = happy && current === "idle" ? "happy" : current;
          target.set(EMOTION_COLORS[mood] ?? "#2f8fff");
          color.lerp(target, 0.06);
          for (const m of [lineMaterial, contourMaterial, pointMaterial] as Material[]) {
            (m as unknown as { color: { copy: (c: unknown) => void } }).color.copy(color);
          }
          const flicker = 0.92 + Math.random() * 0.08;
          contourMaterial.opacity = (current === "halted" ? 0.35 : 0.75) * flicker;

          // Mouth: real voice volume, otherwise a natural open and close
          const level =
            current === "speaking"
              ? Math.max(speechLevel(), 0.35 + 0.35 * Math.sin(time / 95) * Math.sin(time / 37))
              : 0;
          mouth += (level - mouth) * 0.35;
          smile += ((mood === "happy" ? 1 : 0) - smile) * 0.08;
          for (let i = 0; i < base.length; i++) {
            const v = base[i] ?? [0, 0, 0];
            const drop = (jaw[i] ?? 0) * mouth * 7;
            const lift = (cornerWeights[i] ?? 0) * smile * 3;
            positions[i * 3] = (v[0] ?? 0) * (1 + (cornerWeights[i] ?? 0) * smile * 0.05);
            positions[i * 3 + 1] = (v[1] ?? 0) - drop + lift;
            positions[i * 3 + 2] = v[2] ?? 0;
          }
          attribute.needsUpdate = true;

          // Look at you (camera), otherwise drift gently
          const looking = performance.now() - gaze.seen < 4000;
          const yaw = looking ? gaze.x * 0.45 : Math.sin(time / 3100) * 0.18;
          let pitch = looking ? -gaze.y * 0.25 : Math.sin(time / 4300) * 0.06;
          if (current === "listening") pitch += Math.sin(time / 260) * 0.03; // nod
          head.rotation.y += (yaw - head.rotation.y) * 0.08;
          head.rotation.x += (pitch - head.rotation.x) * 0.08;
          head.position.y = Math.sin(time / 1400) * 3;

          // Blink
          let eyeScale = 1;
          if (time > nextBlink) {
            const t = time - nextBlink;
            eyeScale = t < 160 ? Math.abs(1 - t / 80) : 1;
            if (t >= 160) nextBlink = time + 2500 + Math.random() * 3500;
          }
          const eyeGlow = current === "listening" ? 1.35 : current === "halted" ? 0.4 : 1;
          for (const eye of eyes) {
            eye.scale.set(22 * eyeGlow, 12 * eyeGlow * eyeScale, 1);
            eye.material.color.copy(color);
          }
          composer.render();
          frame = requestAnimationFrame(tick);
        };
        frame = requestAnimationFrame(tick);

        observer = new ResizeObserver(() => {
          const w = host.clientWidth;
          const h = host.clientHeight;
          renderer.setSize(w, h);
          composer.setSize(w, h);
          camera.aspect = w / Math.max(1, h);
          camera.updateProjectionMatrix();
        });
        observer.observe(host);
        cleanup = () => {
          renderer.dispose();
          renderer.domElement.remove();
        };
      } catch (error) {
        reportError("hologram", error);
        if (!disposed) setFailed(true);
      }
    })();

    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      observer?.disconnect();
      cleanup?.();
    };
  }, []);

  return (
    <div className={`hologram state-${state}`} ref={hostRef}>
      <div className="holo-scan" />
      {failed && <HudCore state={state} />}
    </div>
  );
}
