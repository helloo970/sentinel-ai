import { useEffect, useRef, useState } from "react";
import type { Group, Line, Object3D, Sprite } from "three";
import type { GeometryCollection, Topology } from "topojson-specification";

export interface GlobeFocus {
  code: string;
  lat: number;
  lon: number;
}

interface Props {
  focus: GlobeFocus | null;
  /** Countries of the tour – each gets a line from home */
  stops: GlobeFocus[];
  home?: { lat: number; lon: number };
}

const R = 100;
const HOME = { lat: 51.2, lon: 10.4 };

/** 3D globe like in a situation room: dark earth, glowing borders, lines to the countries. */
export function Globe3D({ focus, stops, home = HOME }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const api = useRef<{
    setFocus: (f: GlobeFocus | null) => void;
    setStops: (s: GlobeFocus[]) => void;
  } | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    let disposed = false;
    let frame = 0;
    let observer: ResizeObserver | null = null;
    let cleanup: (() => void) | null = null;

    void (async () => {
      try {
        const [THREE, topo, worldModule, post] = await Promise.all([
          import("three"),
          import("topojson-client"),
          import("world-atlas/countries-110m.json"),
          Promise.all([
            import("three/examples/jsm/postprocessing/EffectComposer.js"),
            import("three/examples/jsm/postprocessing/RenderPass.js"),
            import("three/examples/jsm/postprocessing/UnrealBloomPass.js"),
          ]),
        ]);
        if (disposed) return;
        const [{ EffectComposer }, { RenderPass }, { UnrealBloomPass }] = post;

        const toVec = (lat: number, lon: number, r = R) => {
          const phi = ((90 - lat) * Math.PI) / 180;
          const theta = ((lon + 180) * Math.PI) / 180;
          return new THREE.Vector3(
            -r * Math.sin(phi) * Math.cos(theta),
            r * Math.cos(phi),
            r * Math.sin(phi) * Math.sin(theta),
          );
        };

        const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        renderer.setSize(host.clientWidth, host.clientHeight);
        host.appendChild(renderer.domElement);
        const scene = new THREE.Scene();
        const camera = new THREE.PerspectiveCamera(
          40,
          host.clientWidth / host.clientHeight,
          1,
          2000,
        );
        camera.position.set(0, 0, 430);

        const globe: Group = new THREE.Group();
        scene.add(globe);
        globe.add(
          new THREE.Mesh(
            new THREE.SphereGeometry(R, 64, 48),
            new THREE.MeshBasicMaterial({ color: "#05070b" }),
          ),
        );

        // Borders and coasts as glowing lines
        const world = (worldModule.default ?? worldModule) as unknown as Topology<{
          countries: GeometryCollection;
        }>;
        const mesh = topo.mesh(world, world.objects.countries) as unknown as {
          coordinates: [number, number][][];
        };
        const positions: number[] = [];
        for (const line of mesh.coordinates) {
          for (let i = 1; i < line.length; i++) {
            const a = line[i - 1];
            const b = line[i];
            if (!a || !b) continue;
            const va = toVec(a[1], a[0], R + 0.3);
            const vb = toVec(b[1], b[0], R + 0.3);
            positions.push(va.x, va.y, va.z, vb.x, vb.y, vb.z);
          }
        }
        const borders = new THREE.BufferGeometry();
        borders.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
        globe.add(
          new THREE.LineSegments(
            borders,
            new THREE.LineBasicMaterial({ color: "#3b82f6", transparent: true, opacity: 0.45 }),
          ),
        );

        // Glowing rim (atmosphere)
        const glowCanvas = document.createElement("canvas");
        glowCanvas.width = glowCanvas.height = 256;
        const ctx = glowCanvas.getContext("2d");
        if (ctx) {
          const g = ctx.createRadialGradient(128, 128, 70, 128, 128, 128);
          g.addColorStop(0, "rgba(59,130,246,0.0)");
          g.addColorStop(0.6, "rgba(59,130,246,0.07)");
          g.addColorStop(1, "rgba(59,130,246,0)");
          ctx.fillStyle = g;
          ctx.fillRect(0, 0, 256, 256);
        }
        const halo = new THREE.Sprite(
          new THREE.SpriteMaterial({
            map: new THREE.CanvasTexture(glowCanvas),
            transparent: true,
            depthWrite: false,
            blending: THREE.AdditiveBlending,
          }),
        );
        halo.scale.set(R * 2.7, R * 2.7, 1);
        scene.add(halo);

        const markerTexture = (() => {
          const c = document.createElement("canvas");
          c.width = c.height = 64;
          const x = c.getContext("2d");
          if (x) {
            const g = x.createRadialGradient(32, 32, 0, 32, 32, 32);
            g.addColorStop(0, "rgba(255,255,255,1)");
            g.addColorStop(0.3, "rgba(147,197,253,0.8)");
            g.addColorStop(1, "rgba(96,165,250,0)");
            x.fillStyle = g;
            x.fillRect(0, 0, 64, 64);
          }
          return new THREE.CanvasTexture(c);
        })();
        const marker = (lat: number, lon: number, size: number): Sprite => {
          const sprite = new THREE.Sprite(
            new THREE.SpriteMaterial({
              map: markerTexture,
              color: "#93c5fd",
              transparent: true,
              depthWrite: false,
              blending: THREE.AdditiveBlending,
            }),
          );
          sprite.position.copy(toVec(lat, lon, R + 1.5));
          sprite.scale.set(size, size, 1);
          return sprite;
        };
        globe.add(marker(home.lat, home.lon, 5));

        // Lines from home to the countries of the tour
        let overlays: Object3D[] = [];
        let current: { line: Line; total: number; dot: Sprite } | null = null;
        const arc = (lat: number, lon: number, bright: boolean) => {
          const a = toVec(home.lat, home.lon, R);
          const b = toVec(lat, lon, R);
          const mid = a.clone().add(b).multiplyScalar(0.5);
          const lift = 1 + Math.min(0.55, a.distanceTo(b) / (R * 2.4));
          mid.setLength(R * lift);
          const points = new THREE.QuadraticBezierCurve3(a, mid, b).getPoints(64);
          const line = new THREE.Line(
            new THREE.BufferGeometry().setFromPoints(points),
            new THREE.LineBasicMaterial({
              color: bright ? "#bfdbfe" : "#60a5fa",
              transparent: true,
              opacity: bright ? 1 : 0.35,
            }),
          );
          return { line, total: points.length };
        };
        const setStops = (list: GlobeFocus[]) => {
          overlays.forEach((o) => globe.remove(o));
          overlays = [];
          for (const stop of list) {
            const { line } = arc(stop.lat, stop.lon, false);
            const dot = marker(stop.lat, stop.lon, 3.5);
            globe.add(line, dot);
            overlays.push(line, dot);
          }
        };

        let target = { x: 0.35, y: 0 };
        let spinning = true;
        let zoom = 430;
        const setFocus = (f: GlobeFocus | null) => {
          if (current) {
            globe.remove(current.line, current.dot);
            current = null;
          }
          if (!f) {
            spinning = true;
            zoom = 430;
            return;
          }
          const dir = toVec(f.lat, f.lon, 1);
          const yaw = Math.atan2(dir.x, dir.z);
          let y = -yaw;
          // shortest way around the axis
          while (y - globe.rotation.y > Math.PI) y -= Math.PI * 2;
          while (y - globe.rotation.y < -Math.PI) y += Math.PI * 2;
          target = { x: (f.lat * Math.PI) / 180, y };
          spinning = false;
          zoom = 330;
          const { line, total } = arc(f.lat, f.lon, true);
          line.geometry.setDrawRange(0, 0);
          const dot = marker(f.lat, f.lon, 8);
          globe.add(line, dot);
          current = { line, total, dot };
        };
        api.current = { setFocus, setStops };

        const composer = new EffectComposer(renderer);
        composer.addPass(new RenderPass(scene, camera));
        composer.addPass(
          new UnrealBloomPass(
            new THREE.Vector2(host.clientWidth, host.clientHeight),
            0.55,
            0.4,
            0.25,
          ),
        );

        let drawn = 0;
        const tick = (time: number) => {
          if (spinning) {
            globe.rotation.y += 0.0015;
            globe.rotation.x += (0.35 - globe.rotation.x) * 0.02;
          } else {
            globe.rotation.x += (target.x - globe.rotation.x) * 0.05;
            globe.rotation.y += (target.y - globe.rotation.y) * 0.05;
          }
          camera.position.z += (zoom - camera.position.z) * 0.04;
          if (current) {
            drawn = Math.min(current.total, drawn + 1.2);
            current.line.geometry.setDrawRange(0, Math.floor(drawn));
            current.dot.scale.setScalar(7 + Math.sin(time / 200) * 2);
          } else {
            drawn = 0;
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
      } catch {
        if (!disposed) setFailed(true);
      }
    })();

    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      observer?.disconnect();
      cleanup?.();
      api.current = null;
    };
  }, [home.lat, home.lon]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!api.current) return;
      api.current.setStops(stops);
      api.current.setFocus(focus);
      window.clearInterval(timer);
    }, 100);
    return () => window.clearInterval(timer);
  }, [focus, stops]);

  return (
    <div className="globe-stage" ref={hostRef}>
      {failed && <div className="empty">3D globe not available (WebGL missing).</div>}
    </div>
  );
}
