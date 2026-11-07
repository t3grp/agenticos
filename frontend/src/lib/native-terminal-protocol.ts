/** Keep the rendered terminal and PTY within the same supported dimensions. */
export function nativeTerminalSize(cols: number, rows: number) {
  return { cols: Math.min(240, Math.max(40, cols)), rows: Math.min(80, Math.max(10, rows)) };
}

/** Small UTF-8 chunks leave room for JSON escaping in the broker's frame limit. */
export function* nativeInputChunks(input: string): Generator<string> {
  const bytes = new TextEncoder().encode(input);
  const decoder = new TextDecoder();
  for (let offset = 0; offset < bytes.length;) {
    let end = Math.min(offset + 2048, bytes.length);
    while (end < bytes.length && (bytes[end]! & 0xc0) === 0x80) end -= 1;
    yield decoder.decode(bytes.subarray(offset, end));
    offset = end;
  }
}
