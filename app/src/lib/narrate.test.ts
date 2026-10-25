import { afterEach, describe, expect, it } from "vitest";
import { handleEvent } from "../hooks/useEvents";
import { store } from "../state/store";
import { openedByVoice } from "./narrate";

afterEach(() => {
  store.set({ view: "dashboard", viewTab: null, voiceView: null });
});

describe("narration only for views Jarvis opened", () => {
  it("speaks when Jarvis opened the view by voice", () => {
    handleEvent({ id: 1, kind: "navigate", ts: 0, data: { view: "markets" } });
    expect(store.get().view).toBe("markets");
    expect(openedByVoice("markets")).toBe(true);
    expect(openedByVoice("kpis")).toBe(false);
  });

  it("stays silent on a manual click", () => {
    store.set({ view: "kpis", viewTab: null });
    expect(openedByVoice("kpis")).toBe(false);
  });

  it("stays silent when the voice open is old", () => {
    store.set({ view: "markets", voiceView: { view: "markets", at: Date.now() - 60_000 } });
    expect(openedByVoice("markets")).toBe(false);
  });
});
