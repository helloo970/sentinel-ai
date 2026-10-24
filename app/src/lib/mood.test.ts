import { describe, expect, it } from "vitest";
import type { Status } from "../api/types";
import { deriveMood } from "./coreState";

function status(alerts: Partial<Status["alerts"]>, extra: Partial<Status> = {}): Status {
  return {
    halted: false,
    pending_approvals: 0,
    alerts: { info: 0, warn: 0, critical: 0, ...alerts },
    ...extra,
  } as Status;
}

describe("deriveMood", () => {
  it("is calm without warnings", () => {
    expect(deriveMood(null)).toBe("calm");
    expect(deriveMood(status({ info: 4 }))).toBe("calm");
  });
  it("watches on warnings or open approvals", () => {
    expect(deriveMood(status({ warn: 1 }))).toBe("watch");
    expect(deriveMood(status({}, { pending_approvals: 2 }))).toBe("watch");
  });
  it("alarms on critical and shows the emergency stop first", () => {
    expect(deriveMood(status({ critical: 1, warn: 3 }))).toBe("alarm");
    expect(deriveMood(status({ critical: 1 }, { halted: true }))).toBe("halted");
  });
});
