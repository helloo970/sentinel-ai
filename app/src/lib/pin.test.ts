import { describe, expect, it } from "vitest";
import { store } from "../state/store";
import { answerPin, askPin } from "./pin";

describe("askPin", () => {
  it("resolves with a valid PIN and closes the dialog", async () => {
    const pending = askPin("Test");
    expect(store.get().pinRequest?.title).toBe("Test");
    answerPin("482913");
    await expect(pending).resolves.toBe("482913");
    expect(store.get().pinRequest).toBeNull();
  });

  it("rejects malformed input and cancel", async () => {
    const short = askPin("a");
    answerPin("12ab");
    await expect(short).resolves.toBeNull();
    const cancelled = askPin("b");
    answerPin(null);
    await expect(cancelled).resolves.toBeNull();
  });

  it("cancels an older open request when a new one starts", async () => {
    const first = askPin("1");
    const second = askPin("2");
    await expect(first).resolves.toBeNull();
    answerPin("123456");
    await expect(second).resolves.toBe("123456");
  });
});
