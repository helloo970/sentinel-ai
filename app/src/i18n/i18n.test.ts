import { describe, expect, it } from "vitest";
import en from "./en.json";
import { initI18n, resources } from "./index";

function keys(value: unknown, prefix = ""): string[] {
  if (typeof value !== "object" || value === null) return [prefix];
  return Object.entries(value).flatMap(([k, v]) => keys(v, prefix ? `${prefix}.${k}` : k));
}

describe("translations", () => {
  it("ships English only", () => {
    expect(Object.keys(resources)).toEqual(["en"]);
    const i18n = initI18n();
    expect(i18n.language).toBe("en");
  });

  it("has no empty strings", () => {
    const empty = keys(en).filter((k) => {
      const value = k
        .split(".")
        .reduce<unknown>((o, part) => (o as Record<string, unknown>)[part], en);
      return typeof value === "string" && !value.trim();
    });
    expect(empty).toEqual([]);
  });
});
