import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { handleEvent } from "../hooks/useEvents";
import { store } from "../state/store";
import { focusTransform, WorldMap } from "./WorldMap";

afterEach(() => {
  cleanup();
  store.set({ view: "dashboard", tourRequest: 0 });
});

describe("WorldMap", () => {
  it("renders all countries and highlights the focused one", () => {
    const { container } = render(
      <WorldMap focus={{ code: "643", lon: 60, lat: 58, zoom: 2.2 }} visited={["804"]} />,
    );
    expect(container.querySelectorAll("path.country").length).toBeGreaterThan(150);
    expect(container.querySelectorAll("path.country.active").length).toBe(1);
    expect(container.querySelector(".ping")).toBeTruthy();
  });

  it("centres the focus point and clamps the zoom", () => {
    expect(focusTransform(null)).toEqual({ x: 0, y: 0, k: 1 });
    const russia = focusTransform({ code: "643", lon: 60, lat: 58, zoom: 50 });
    expect(russia.k).toBe(10);
    const germany = focusTransform({ code: "276", lon: 10.4, lat: 51.2, zoom: 6 });
    expect(germany.k).toBe(6);
    expect(Number.isFinite(germany.x) && Number.isFinite(germany.y)).toBe(true);
  });
});

describe("wake event", () => {
  it("switches to the world view and requests a tour", () => {
    handleEvent({ id: 1, kind: "wake", ts: 0, data: { reason: "clap" } });
    expect(store.get().view).toBe("world");
    expect(store.get().tourRequest).toBeGreaterThan(0);
  });
});
