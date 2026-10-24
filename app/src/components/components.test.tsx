import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it } from "vitest";
import { initI18n } from "../i18n";
import { handleEvent } from "../hooks/useEvents";
import { store } from "../state/store";
import { HudCore } from "./HudCore";
import { Toasts } from "./Toasts";

beforeAll(() => {
  initI18n();
});

afterEach(() => {
  cleanup();
  store.set({ toasts: [], voiceState: "off", alertsVersion: 0 });
});

describe("HudCore", () => {
  it("exposes its state for styling and accessibility", () => {
    const { container } = render(<HudCore state="listening" />);
    const core = container.querySelector(".core");
    expect(core?.getAttribute("data-state")).toBe("listening");
    expect(screen.getByRole("img").getAttribute("aria-label")).toBe("Listening");
    expect(container.querySelectorAll(".ring").length).toBeGreaterThanOrEqual(6);
  });
});

describe("event handling", () => {
  it("shows an undo toast for autonomous actions", () => {
    handleEvent({
      id: 1,
      kind: "action",
      ts: 0,
      data: {
        title: "Quarantine",
        message: "x.exe quarantined",
        autonomous: true,
        undo_id: "abc",
        undo_expires: Date.now() / 1000 + 60,
      },
    });
    render(<Toasts />);
    expect(screen.getByText("Quarantine · autonomous")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Undo/ })).toBeTruthy();
  });

  it("tracks voice state and alert version", () => {
    handleEvent({ id: 2, kind: "voice", ts: 0, data: { state: "speaking" } });
    handleEvent({ id: 3, kind: "alert", ts: 0, data: { severity: "warn" } });
    expect(store.get().voiceState).toBe("speaking");
    expect(store.get().alertsVersion).toBe(1);
  });

  it("ignores unknown events", () => {
    handleEvent({ id: 4, kind: "unknown", ts: 0, data: {} });
    expect(store.get().toasts).toEqual([]);
  });
});
