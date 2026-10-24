import { describe, expect, it } from "vitest";
import { readSse, SseParser } from "./sse";

describe("SseParser", () => {
  it("parses complete events across chunk boundaries", () => {
    const parser = new SseParser();
    expect(parser.push('data: {"a":')).toEqual([]);
    expect(parser.push('1}\n\ndata: {"b":2}\n\n')).toEqual([{ a: 1 }, { b: 2 }]);
  });

  it("ignores comments, keep-alives and invalid JSON", () => {
    const parser = new SseParser();
    expect(parser.push(': ping\n\ndata: nope\n\ndata: {"ok":true}\r\n\r\n')).toEqual([
      { ok: true },
    ]);
  });
});

describe("readSse", () => {
  it("yields events from a byte stream", async () => {
    const encoder = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('data: {"type":"text","text":"Hel'));
        controller.enqueue(encoder.encode('lo"}\n\ndata: {"type":"done"}\n\n'));
        controller.close();
      },
    });
    const events: unknown[] = [];
    for await (const event of readSse(body)) events.push(event);
    expect(events).toEqual([{ type: "text", text: "Hello" }, { type: "done" }]);
  });
});
