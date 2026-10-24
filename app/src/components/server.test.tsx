import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { countryName, toMapCode } from "../lib/countries";
import { seriesValue } from "./ServerPanels";
import { WorldMap } from "./WorldMap";

afterEach(cleanup);

describe("country helpers", () => {
  it("maps ISO alpha-2 to map codes and English names", () => {
    expect(toMapCode("RU")).toBe("643");
    expect(toMapCode("us")).toBe("840");
    expect(toMapCode("LAN")).toBeNull();
    expect(toMapCode(null)).toBeNull();
    expect(countryName("CN")).toBe("China");
    expect(countryName("LAN")).toBe("Home network");
    expect(countryName(null)).toBe("unknown");
  });
});

describe("server history", () => {
  it("normalises load by CPU count and caps at 100 %", () => {
    const point = {
      ts: 0,
      load1: 2,
      cpu_count: 4,
      memory_percent: 40,
      disk_percent: 70,
      auth_failures: 0,
    };
    expect(seriesValue(point, "load")).toBe(50);
    expect(seriesValue({ ...point, load1: 99 }, "load")).toBe(100);
    expect(seriesValue(point, "disk")).toBe(70);
  });
});

describe("attack heat map", () => {
  it("colours attacked countries", () => {
    const { container } = render(<WorldMap focus={null} heat={{ "643": 12, "156": 3 }} />);
    expect(container.querySelectorAll("path.country.heat").length).toBe(2);
    expect(container.querySelector("path.country.heat title")?.textContent).toContain(
      "failed attempts",
    );
  });
});
