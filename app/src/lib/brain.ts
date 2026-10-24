/** Brain: backend data → nodes and lines for the 3D view (laid out like a body). */

export interface BrainCategory {
  key: string;
  label: string;
  builtin: number;
}

export interface BrainNode {
  id: number;
  label: string;
  category: string;
  summary: string;
  origin: string;
  source: string;
  hits: number;
  updated: number;
}

export interface BrainGraphData {
  categories: BrainCategory[];
  nodes: BrainNode[];
  edges: { a: number; b: number; relation: string }[];
}

/** The four parts: brain (knowledge), voice, hand (skills) and the ring of sources. The keys are data ids shared with the backend. */
export type Part = "gehirn" | "stimme" | "hand" | "quellen";

export interface GraphNode {
  id: string;
  label: string;
  kind: "part" | "hub" | "source" | "thought";
  part: Part;
  color: string;
  size: number;
  labeled: boolean;
  nodeId?: number;
  category?: string;
  fx?: number;
  fy?: number;
  fz?: number;
}

export interface GraphLink {
  source: string;
  target: string;
  kind: "hub" | "edge" | "feed" | "tunnel";
}

/** Human-readable names of the parts (the keys stay German, they are data ids). */
export const PART_LABELS: Record<Part, string> = {
  gehirn: "Brain",
  stimme: "Voice",
  hand: "Hand",
  quellen: "Sources",
};

export const PART_COLORS: Record<Part, string> = {
  gehirn: "#eef4ff",
  stimme: "#93c5fd",
  hand: "#3b82f6",
  quellen: "#bfdbfe",
};
const THOUGHT_COLORS: Record<Part, string> = {
  gehirn: "#dbe4f0",
  stimme: "#bfdbfe",
  hand: "#60a5fa",
  quellen: "#a5c8fb",
};
// Fixed positions of the parts in space and the ring of sources
const PART_POSITIONS: Record<Exclude<Part, "quellen">, [number, number, number]> = {
  gehirn: [0, 90, 0],
  stimme: [-130, -130, 40],
  hand: [170, -60, -30],
};
export const RING = { rx: 320, rz: 200, y: -10 };
// Connections between the parts and what flows through them
export const TUNNELS: { from: Part; to: Part; key: string }[] = [
  { from: "quellen", to: "gehirn", key: "feed" },
  { from: "gehirn", to: "hand", key: "knows" },
  { from: "hand", to: "stimme", key: "reports" },
  { from: "stimme", to: "gehirn", key: "remembers" },
];

export function partOf(category: string): Part {
  if (category === "skills") return "hand";
  if (category === "stimme") return "stimme";
  if (category === "quellen") return "quellen";
  return "gehirn";
}

export function nodeColor(category: string): string {
  return THOUGHT_COLORS[partOf(category)];
}

export const nodeKey = (id: number) => `n:${id}`;

const isHandGroup = (n: BrainNode) =>
  n.source.startsWith("sentinel:group:") || n.source === "sentinel:jarvis";

export function partCounts(data: BrainGraphData): Record<Part | "ich", number> {
  const counts = { gehirn: 0, stimme: 0, hand: 0, quellen: 0, ich: 0 };
  for (const n of data.nodes) {
    counts[partOf(n.category)] += 1;
    if (n.category === "ich") counts.ich += 1;
  }
  return counts;
}

export function buildGraph(data: BrainGraphData): { nodes: GraphNode[]; links: GraphLink[] } {
  const nodes: GraphNode[] = [];
  const links: GraphLink[] = [];
  for (const part of ["gehirn", "stimme", "hand"] as const) {
    const [fx, fy, fz] = PART_POSITIONS[part];
    nodes.push({
      id: `p:${part}`,
      label: PART_LABELS[part].toUpperCase(),
      kind: "part",
      part,
      color: PART_COLORS[part],
      size: 9,
      labeled: true,
      fx,
      fy,
      fz,
    });
  }
  links.push(
    { source: "p:gehirn", target: "p:hand", kind: "tunnel" },
    { source: "p:hand", target: "p:stimme", kind: "tunnel" },
    { source: "p:stimme", target: "p:gehirn", kind: "tunnel" },
  );

  // Sub-areas of the brain (categories with content)
  const used = new Set(data.nodes.map((n) => n.category));
  for (const cat of data.categories) {
    if (!used.has(cat.key) || partOf(cat.key) !== "gehirn") continue;
    nodes.push({
      id: `c:${cat.key}`,
      label: cat.label,
      kind: "hub",
      part: "gehirn",
      color: PART_COLORS.gehirn,
      size: 4,
      labeled: true,
      category: cat.key,
    });
    links.push({ source: "p:gehirn", target: `c:${cat.key}`, kind: "hub" });
  }

  const byId = new Map(data.nodes.map((n) => [n.id, n]));
  // Skills hang off their group, not directly off the hand
  const handLinked = new Set<number>();
  for (const e of data.edges) {
    const a = byId.get(e.a);
    const b = byId.get(e.b);
    if (a && b && partOf(a.category) === "hand" && partOf(b.category) === "hand") {
      if (isHandGroup(a) && !isHandGroup(b)) handLinked.add(b.id);
      if (isHandGroup(b) && !isHandGroup(a)) handLinked.add(a.id);
    }
  }

  const sources = data.nodes.filter((n) => partOf(n.category) === "quellen");
  sources.forEach((n, index) => {
    const angle = (index / Math.max(1, sources.length)) * Math.PI * 2 + 0.4;
    nodes.push({
      id: nodeKey(n.id),
      label: n.label,
      kind: "source",
      part: "quellen",
      color: PART_COLORS.quellen,
      size: 2.6,
      labeled: true,
      nodeId: n.id,
      category: n.category,
      fx: Math.cos(angle) * RING.rx,
      fy: RING.y + Math.sin(angle * 2) * 12,
      fz: Math.sin(angle) * RING.rz,
    });
    links.push({ source: nodeKey(n.id), target: "p:gehirn", kind: "feed" });
  });

  for (const n of data.nodes) {
    const part = partOf(n.category);
    if (part === "quellen") continue;
    const group = part === "hand" && isHandGroup(n);
    nodes.push({
      id: nodeKey(n.id),
      label: n.label,
      kind: "thought",
      part,
      color: group ? PART_COLORS.hand : THOUGHT_COLORS[part],
      size: (group ? 3 : 1.4) + Math.min(3, Math.log2(1 + n.hits)),
      labeled: group || part === "stimme",
      nodeId: n.id,
      category: n.category,
    });
    if (part === "gehirn") {
      links.push({ source: `c:${n.category}`, target: nodeKey(n.id), kind: "hub" });
    } else if (part === "stimme" || group || !handLinked.has(n.id)) {
      links.push({ source: `p:${part}`, target: nodeKey(n.id), kind: "hub" });
    }
  }
  for (const e of data.edges) {
    if (byId.has(e.a) && byId.has(e.b)) {
      links.push({ source: nodeKey(e.a), target: nodeKey(e.b), kind: "edge" });
    }
  }
  // Links to missing nodes (e.g. unknown category) crash the simulation
  const ids = new Set(nodes.map((n) => n.id));
  return { nodes, links: links.filter((l) => ids.has(l.source) && ids.has(l.target)) };
}

const ORIGIN_LABELS: Record<string, string> = {
  user: "from you",
  chat: "from a conversation",
  seed: "from setup",
  system: "from SENTINEL",
  ai: "from the AI",
  file: "from a file",
};

export function originLabel(origin: string): string {
  return ORIGIN_LABELS[origin] ?? origin;
}
