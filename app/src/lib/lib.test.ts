import { describe, expect, it } from "vitest";
import type { Status } from "../api/types";
import { deriveCoreState } from "./coreState";
import { formatAgo, formatEuro, percentTone, remainingSeconds, severityTone } from "./format";

function status(patch: Partial<Status> = {}): Status {
  return {
    version: "0.1.0",
    user_name: "Alex",
    autonomy: 2,
    halted: false,
    autonomous_last_hour: 0,
    autonomous_limit: 10,
    undo_window_s: 60,
    llm: { provider: "offline", model: "offline", available: false },
    costs: { today_eur: 0, month_eur: 0, daily_limit_eur: 2, monthly_limit_eur: 30, model: "x" },
    alerts: { info: 0, warn: 0, critical: 0 },
    pending_approvals: 0,
    monitor: { last_scan: null, last_error: null, sysmon: "", watched_folders: [], interval_s: 30 },
    voice: {
      state: "off",
      enabled: false,
      capabilities: {},
      error: null,
      last_transcript: null,
      wakeword: true,
      clap: true,
    },
    server: { configured: false, error: null },
    time: 0,
    ...patch,
  };
}

describe("deriveCoreState", () => {
  it("prioritises the emergency stop", () => {
    expect(deriveCoreState(status({ halted: true }), "speaking")).toBe("halted");
  });
  it("shows voice states before alerts", () => {
    const s = status({ alerts: { info: 0, warn: 0, critical: 2 } });
    expect(deriveCoreState(s, "listening")).toBe("listening");
    expect(deriveCoreState(s, "idle")).toBe("critical");
  });
  it("maps warnings and busy state", () => {
    expect(deriveCoreState(status({ alerts: { info: 0, warn: 1, critical: 0 } }), "idle")).toBe(
      "alert",
    );
    expect(deriveCoreState(status(), "idle", true)).toBe("thinking");
    expect(deriveCoreState(null, "off")).toBe("idle");
  });
});

describe("format helpers", () => {
  it("formats relative times", () => {
    expect(formatAgo(100, 130)).toBe("30 s ago");
    expect(formatAgo(0)).toBe("–");
    expect(formatAgo(0 + 1, 7201)).toBe("2 h ago");
  });
  it("formats euro and tones", () => {
    expect(formatEuro(0.0123)).toBe("0.012 €");
    expect(formatEuro(12.5)).toBe("12.50 €");
    expect(percentTone(50)).toBe("ok");
    expect(percentTone(80)).toBe("warn");
    expect(percentTone(95)).toBe("crit");
    expect(severityTone("critical")).toBe("crit");
  });
  it("never returns negative undo seconds", () => {
    expect(remainingSeconds(100, 200_000)).toBe(0);
    expect(remainingSeconds(130, 100_000)).toBe(30);
    expect(remainingSeconds(null)).toBe(0);
  });
});
