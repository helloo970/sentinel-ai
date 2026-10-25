/** Splits a raw MJPEG byte stream into single JPEG frames (start FFD8, end FFD9). */
export class MjpegSplitter {
  private buffer = new Uint8Array(0);

  push(chunk: Uint8Array): Uint8Array[] {
    const joined = new Uint8Array(this.buffer.length + chunk.length);
    joined.set(this.buffer);
    joined.set(chunk, this.buffer.length);
    const frames: Uint8Array[] = [];
    let start = -1;
    let consumed = 0;
    for (let i = 0; i < joined.length - 1; i++) {
      if (joined[i] !== 0xff) continue;
      if (joined[i + 1] === 0xd8 && start < 0) start = i;
      else if (joined[i + 1] === 0xd9 && start >= 0) {
        frames.push(joined.slice(start, i + 2));
        consumed = i + 2;
        start = -1;
      }
    }
    // Keep the unfinished frame; drop garbage before it
    this.buffer = joined.slice(start >= 0 ? start : consumed);
    if (this.buffer.length > 8_000_000) this.buffer = new Uint8Array(0);
    return frames;
  }
}
