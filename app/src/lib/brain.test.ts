import { afterEach, describe, expect, it } from "vitest";
import { handleEvent } from "../hooks/useEvents";
import { store } from "../state/store";
import { buildGraph, originLabel, partCounts, partOf } from "./brain";

const data = {
  categories: [
    { key: "ich", label: "Me & people", builtin: 1 },
    { key: "skills", label: "Skills & Tools", builtin: 1 },
    { key: "musik", label: "Music", builtin: 0 },
  ],
  nodes: [
    {
      id: 1,
      label: "Gaming",
      category: "ich",
      summary: "",
      origin: "seed",
      source: "setup",
      hits: 0,
      updated: 0,
    },
    {
      id: 2,
      label: "Python",
      category: "musik",
      summary: "",
      origin: "ai",
      source: "x",
      hits: 7,
      updated: 0,
    },
    {
      id: 3,
      label: "YouTube",
      category: "quellen",
      summary: "",
      origin: "user",
      source: "profile",
      hits: 0,
      updated: 0,
    },
  ],
  edges: [
    { a: 1, b: 2, relation: "" },
    { a: 1, b: 99, relation: "" },
  ],
};

afterEach(() => {
  store.set({ brainActive: [], brainFocus: null, brainVersion: 0, view: "dashboard" });
});

describe("brain graph", () => {
  it("builds the body: parts, brain areas, sources on the ring", () => {
    const { nodes, links } = buildGraph(data);
    const ids = nodes.map((n) => n.id);
    expect(ids.slice(0, 3)).toEqual(["p:gehirn", "p:stimme", "p:hand"]);
    // brain areas only for categories with content
    expect(ids).toContain("c:ich");
    expect(ids).not.toContain("c:skills");
    expect(links).toContainEqual({ source: "n:1", target: "n:2", kind: "edge" });
    expect(links.some((l) => l.target === "n:99")).toBe(false);
    const source = nodes.find((n) => n.id === "n:3");
    expect(source?.kind).toBe("source");
    expect(source?.fx).toBeDefined();
    expect(links).toContainEqual({ source: "n:3", target: "p:gehirn", kind: "feed" });
    expect(links.filter((l) => l.kind === "tunnel")).toHaveLength(3);
  });

  it("never links to nodes that are missing", () => {
    const orphan = {
      ...data,
      nodes: [{ ...data.nodes[0]!, id: 5, category: "unbekannt" }],
      edges: [],
    };
    const { nodes, links } = buildGraph(orphan);
    const ids = new Set(nodes.map((n) => n.id));
    expect(links.every((l) => ids.has(l.source) && ids.has(l.target))).toBe(true);
  });

  it("counts parts and maps categories", () => {
    expect(partOf("skills")).toBe("hand");
    expect(partOf("musik")).toBe("gehirn");
    expect(partCounts(data)).toMatchObject({ gehirn: 2, hand: 0, quellen: 1, ich: 1 });
    expect(originLabel("file")).toBe("from a file");
  });

  it("reacts to live brain events", () => {
    handleEvent({ id: 1, kind: "brain_active", ts: 0, data: { ids: [3, 4] } });
    expect(store.get().brainActive).toEqual([3, 4]);
    handleEvent({ id: 2, kind: "brain_focus", ts: 0, data: { id: 3, forget: true } });
    expect(store.get().view).toBe("brain");
    expect(store.get().brainFocus).toMatchObject({ id: 3, forget: true });
    handleEvent({ id: 3, kind: "brain_changed", ts: 0, data: {} });
    expect(store.get().brainVersion).toBe(1);
  });
});
