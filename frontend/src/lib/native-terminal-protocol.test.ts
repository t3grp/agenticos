import { expect, it } from "vitest";
import { nativeInputChunks, nativeTerminalSize } from "./native-terminal-protocol";

it("keeps mobile and large terminals inside the PTY bounds", () => {
  expect(nativeTerminalSize(20, 5)).toEqual({ cols: 40, rows: 10 });
  expect(nativeTerminalSize(500, 200)).toEqual({ cols: 240, rows: 80 });
  expect(nativeTerminalSize(120, 32)).toEqual({ cols: 120, rows: 32 });
});

it.each(["", "short", "a".repeat(2047) + "界🙂".repeat(1600), "\u0001".repeat(6000)])(
  "chunks pasted text without splitting Unicode or exceeding the escaped frame limit",
  (input) => {
    const chunks = [...nativeInputChunks(input)];
    expect(chunks.join("")).toBe(input);
    for (const data of chunks) {
      expect(new TextEncoder().encode(data).byteLength).toBeLessThanOrEqual(2048);
      expect(
        new TextEncoder().encode(JSON.stringify({ type: "input", data })).byteLength,
      ).toBeLessThan(16_384);
    }
  },
);
