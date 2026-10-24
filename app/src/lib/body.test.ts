import { describe, expect, it } from "vitest";
import { nextView } from "./body";
import { GestureTracker } from "./vision";

describe("gestures", () => {
  it("fires a held gesture once and then cools down", () => {
    const tracker = new GestureTracker();
    expect(tracker.update(0, "Open_Palm", null)).toBeNull();
    expect(tracker.update(300, "Open_Palm", null)).toBeNull();
    expect(tracker.update(500, "Open_Palm", null)).toBe("open_palm");
    expect(tracker.update(900, "Open_Palm", null)).toBeNull(); // cooldown
    expect(tracker.update(3000, "Pointing_Up", null)).toBeNull(); // unknown gesture
  });

  it("detects swipes from wrist movement (mirrored camera)", () => {
    const tracker = new GestureTracker();
    tracker.update(0, null, 0.7);
    tracker.update(100, null, 0.6);
    expect(tracker.update(200, null, 0.4)).toBe("swipe_right");
    const other = new GestureTracker();
    other.update(0, null, 0.3);
    other.update(100, null, 0.45);
    expect(other.update(200, null, 0.62)).toBe("swipe_left");
  });

  it("cycles through the views", () => {
    expect(nextView("dashboard", "swipe_right")).toBe("world");
    expect(nextView("dashboard", "swipe_left")).toBe("brain");
    expect(nextView("settings", "swipe_right")).toBe("world");
  });
});
