/** Parser for server-sent events from a fetch stream (EventSource can't send headers). */
export class SseParser {
  private buffer = "";

  /** Takes a chunk of text and returns all complete events. */
  push(chunk: string): unknown[] {
    this.buffer += chunk.replace(/\r\n/g, "\n");
    const events: unknown[] = [];
    let index = this.buffer.indexOf("\n\n");
    while (index !== -1) {
      const block = this.buffer.slice(0, index);
      this.buffer = this.buffer.slice(index + 2);
      const data = block
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).replace(/^ /, ""))
        .join("\n");
      if (data) {
        try {
          events.push(JSON.parse(data));
        } catch {
          // Ignore incomplete or foreign data
        }
      }
      index = this.buffer.indexOf("\n\n");
    }
    return events;
  }
}

export async function* readSse(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<unknown> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser();
  try {
    while (!signal?.aborted) {
      const { value, done } = await reader.read();
      if (done) break;
      for (const event of parser.push(decoder.decode(value, { stream: true }))) {
        yield event;
      }
    }
  } finally {
    reader.releaseLock();
  }
}
