import { useEffect, useRef, useState } from "react";
import type { ForceGraph3DInstance } from "3d-force-graph";
import type { Group, Sprite } from "three";
import { buildGraph, nodeKey, RING, type BrainGraphData, type GraphNode } from "../lib/brain";

type Node3D = GraphNode & { x?: number; y?: number; z?: number };
type Link3D = { source: string | Node3D; target: string | Node3D; kind: string };
type Graph = ForceGraph3DInstance<Node3D, Link3D>;

interface Visual {
  glow: Sprite;
  label?: HTMLElement;
  base: number;
  color: string;
  idle: number;
}

interface Props {
  data: BrainGraphData | null;
  /** Thoughts Jarvis is using right now (they light up) */
  active: number[];
  focus: { id: number; forget: boolean; at: number } | null;
  onSelect: (nodeId: number | null) => void;
}

const LINK_COLORS: Record<string, string> = {
  tunnel: "#60a5fa",
  feed: "#dbeafe",
  edge: "#bfdbfe",
  hub: "#ffffff",
};
const LINK_WIDTHS: Record<string, number> = { tunnel: 1.1, feed: 0.12, edge: 0.3, hub: 0.18 };

const linkEnd = (end: string | Node3D) => (typeof end === "string" ? end : end.id);

/** 3D brain: glowing thoughts in space, rotate and zoom with the mouse. */
export function BrainGraph({ data, active, focus, onSelect }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<Graph | null>(null);
  const visuals = useRef(new Map<string, Visual>());
  // three.js doesn't clean up HTML labels itself when rebuilding
  const labels = useRef(new Set<HTMLElement>());
  const activeRef = useRef(new Set<string>());
  const forgetRef = useRef<string | null>(null);
  const focusRef = useRef<string | null>(null);
  const selectRef = useRef(onSelect);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    selectRef.current = onSelect;
  }, [onSelect]);

  // Once: build the scene, glow effect and animation
  useEffect(() => {
    const host = hostRef.current;
    const visualMap = visuals.current;
    const labelSet = labels.current;
    if (!host) return;
    let disposed = false;
    let frame = 0;
    let observer: ResizeObserver | null = null;

    void (async () => {
      try {
        const [{ default: ForceGraph3D }, THREE, { UnrealBloomPass }, css2d] = await Promise.all([
          import("3d-force-graph"),
          import("three"),
          import("three/examples/jsm/postprocessing/UnrealBloomPass.js"),
          import("three/examples/jsm/renderers/CSS2DRenderer.js"),
        ]);
        if (disposed) return;

        const glowTexture = (() => {
          const canvas = document.createElement("canvas");
          canvas.width = canvas.height = 64;
          const ctx = canvas.getContext("2d");
          if (ctx) {
            const g = ctx.createRadialGradient(32, 32, 0, 32, 32, 32);
            g.addColorStop(0, "rgba(255,255,255,1)");
            g.addColorStop(0.25, "rgba(255,255,255,0.75)");
            g.addColorStop(1, "rgba(255,255,255,0)");
            ctx.fillStyle = g;
            ctx.fillRect(0, 0, 64, 64);
          }
          return new THREE.CanvasTexture(canvas);
        })();

        // Labels as HTML: they stay sharp, the glow effect doesn't blur them
        const textLabel = (text: string, kind: string, color: string) => {
          const element = document.createElement("div");
          element.className = `brain-label ${kind}`;
          element.textContent = text;
          element.style.color = color;
          labels.current.add(element);
          return { element, object: new css2d.CSS2DObject(element) };
        };

        const graph: Graph = new ForceGraph3D(host, {
          controlType: "orbit",
          extraRenderers: [new css2d.CSS2DRenderer() as never],
        }) as unknown as Graph;
        graph
          .backgroundColor("rgba(0,0,0,0)")
          .showNavInfo(false)
          .width(host.clientWidth)
          .height(host.clientHeight)
          .nodeThreeObject((n: Node3D) => {
            const group: Group = new THREE.Group();
            const core = new THREE.Mesh(
              new THREE.SphereGeometry(n.size * 0.35, 16, 12),
              new THREE.MeshBasicMaterial({ color: n.color }),
            );
            const glow = new THREE.Sprite(
              new THREE.SpriteMaterial({
                map: glowTexture,
                color: n.color,
                transparent: true,
                blending: THREE.AdditiveBlending,
                depthWrite: false,
                opacity: n.kind === "thought" ? 0.5 : 0.9,
              }),
            );
            const base = n.size * (n.kind === "part" ? 3.2 : 2.4);
            glow.scale.set(base, base, 1);
            group.add(core, glow);
            const visual: Visual = {
              glow,
              base,
              color: n.color,
              idle: n.kind === "thought" ? 0.5 : 0.9,
            };
            const label = textLabel(n.label, n.kind, n.color);
            label.object.position.set(0, n.size * 0.9 + 3, 0);
            group.add(label.object);
            if (n.labeled) label.element.classList.add("labeled");
            else visual.label = label.element;
            visuals.current.set(n.id, visual);
            return group;
          })
          .linkColor((l: Link3D) => LINK_COLORS[l.kind] ?? "#ffffff")
          .linkOpacity(0.2)
          .linkWidth((l: Link3D) => LINK_WIDTHS[l.kind] ?? 0.2)
          .linkDirectionalParticleColor(() => "#93c5fd")
          .linkDirectionalParticleWidth(1.6)
          .linkDirectionalParticleSpeed(0.012)
          .onNodeClick((n: Node3D) => {
            if (n.nodeId) selectRef.current(n.nodeId);
            const distance = 70;
            const { x = 0, y = 0, z = 0 } = n;
            const ratio = 1 + distance / Math.max(1, Math.hypot(x, y, z));
            graph.cameraPosition({ x: x * ratio, y: y * ratio, z: z * ratio }, { x, y, z }, 1200);
          })
          .onBackgroundClick(() => selectRef.current(null));

        const bloom = new UnrealBloomPass(
          new THREE.Vector2(host.clientWidth, host.clientHeight),
          1.25,
          0.6,
          0.12,
        );
        graph.postProcessingComposer().addPass(bloom);
        const controls = graph.controls() as { autoRotate?: boolean; autoRotateSpeed?: number };
        controls.autoRotate = true;
        controls.autoRotateSpeed = 0.35;
        // Spread the points further apart
        const charge = graph.d3Force("charge") as { strength?: (v: number) => void } | undefined;
        charge?.strength?.(-45);
        const linkForce = graph.d3Force("link") as
          { distance?: (fn: (l: Link3D) => number) => void } | undefined;
        linkForce?.distance?.((l: Link3D) => (l.kind === "edge" ? 26 : 30));
        // Ring of sources
        const ring = new THREE.EllipseCurve(0, 0, RING.rx, RING.rz).getPoints(160);
        const ringLine = new THREE.LineLoop(
          new THREE.BufferGeometry().setFromPoints(
            ring.map((pt) => new THREE.Vector3(pt.x, RING.y, pt.y)),
          ),
          new THREE.LineBasicMaterial({ color: "#cfe0ff", transparent: true, opacity: 0.16 }),
        );
        graph.scene().add(ringLine);
        graphRef.current = graph;

        observer = new ResizeObserver(() => {
          graph.width(host.clientWidth).height(host.clientHeight);
        });
        observer.observe(host);

        // Live glow: active thoughts pulse, "forget" turns red
        const tick = (time: number) => {
          for (const [id, visual] of visuals.current) {
            const on = activeRef.current.has(id) || focusRef.current === id;
            const doomed = forgetRef.current === id;
            const pulse = on || doomed ? 1.9 + 0.35 * Math.sin(time / 180) : 1;
            visual.glow.scale.setScalar(visual.base * pulse);
            visual.glow.material.opacity = on || doomed ? 1 : visual.idle;
            visual.glow.material.color.set(doomed ? "#ff2d6f" : visual.color);
            if (visual.label) visual.label.classList.toggle("on", on || doomed);
          }
          frame = requestAnimationFrame(tick);
        };
        frame = requestAnimationFrame(tick);
        setReady(true);
      } catch {
        if (!disposed) setFailed(true);
      }
    })();

    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      observer?.disconnect();
      graphRef.current?._destructor();
      graphRef.current = null;
      visualMap.clear();
      labelSet.forEach((element) => element.remove());
      labelSet.clear();
    };
  }, []);

  // Data: known points keep their position
  useEffect(() => {
    const graph = graphRef.current;
    if (!graph || !ready || !data) return;
    const previous = new Map(graph.graphData().nodes.map((n) => [n.id, n]));
    const next = buildGraph(data);
    visuals.current.clear();
    labels.current.forEach((element) => element.remove());
    labels.current.clear();
    graph.graphData({
      nodes: next.nodes.map((n) => {
        const old = previous.get(n.id);
        return old && n.fx === undefined ? { ...n, x: old.x, y: old.y, z: old.z } : n;
      }),
      links: next.links,
    });
    // On first load: distance matching the size of the brain
    if (previous.size <= 1) {
      graph.cameraPosition({ x: 0, y: 170, z: 560 }, { x: 0, y: -10, z: 0 });
    }
  }, [data, ready]);

  // Active thoughts: sparks on the connections
  useEffect(() => {
    activeRef.current = new Set(active.map(nodeKey));
    const graph = graphRef.current;
    if (!graph || !ready) return;
    const lit = activeRef.current;
    graph.linkDirectionalParticles((l: Link3D) => {
      if (lit.has(linkEnd(l.source)) || lit.has(linkEnd(l.target))) return 3;
      return l.kind === "tunnel" ? 2 : l.kind === "feed" ? 1 : 0;
    });
  }, [active, ready]);

  // "What do you know about …" / "Forget …": the camera flies to the thought
  useEffect(() => {
    const graph = graphRef.current;
    forgetRef.current = focus?.forget ? nodeKey(focus.id) : null;
    focusRef.current = focus ? nodeKey(focus.id) : null;
    if (!graph || !ready || !focus) return;
    const timer = window.setTimeout(() => {
      const target = graph.graphData().nodes.find((n) => n.id === nodeKey(focus.id));
      if (!target) return;
      const { x = 0, y = 0, z = 0 } = target;
      const ratio = 1 + 60 / Math.max(1, Math.hypot(x, y, z));
      graph.cameraPosition({ x: x * ratio, y: y * ratio, z: z * ratio }, { x, y, z }, 1500);
      selectRef.current(focus.id);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [focus, ready]);

  return (
    <div className="brain-stage" ref={hostRef}>
      {failed && <div className="empty">3D view not available (WebGL missing).</div>}
    </div>
  );
}
