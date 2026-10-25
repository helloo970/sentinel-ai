import { describe, expect, it } from "vitest";
import { MjpegSplitter } from "./mjpeg";

const frame = (n: number) => [0xff, 0xd8, n, n, 0xff, 0xd9];

describe("MJPEG splitter", () => {
  it("finds frames across chunk borders", () => {
    const split = new MjpegSplitter();
    const bytes = [...frame(1), ...frame(2), ...frame(3)];
    expect(split.push(new Uint8Array(bytes.slice(0, 8)))).toHaveLength(1);
    const rest = split.push(new Uint8Array(bytes.slice(8)));
    expect(rest.map((f) => f[2])).toEqual([2, 3]);
  });

  it("ignores bytes before the first frame", () => {
    const split = new MjpegSplitter();
    expect(split.push(new Uint8Array([1, 2, 3, ...frame(7)]))[0]).toEqual(new Uint8Array(frame(7)));
  });
});
