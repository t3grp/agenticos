import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

/**
 * The green brand ramp, measured rather than assumed.
 *
 * The redesign repoints the brand from a desaturated violet-grey
 * (`--brand-h: 265, --brand-c: 0`) to green (hue 155, chroma 0.13), and moves
 * the primary button from the neutral `action` surface onto the brand fill.
 * A fill under white text owes 4.5:1, accent text on its own subtle fill owes
 * the same, and the focus ring owes 3:1 as a non-text mark. Success moved to
 * teal (hue 178) in the same change, so "go" (brand) and "done" (success) are
 * not the same colour - the gap is asserted, not eyeballed.
 *
 * The ramp steps are the `globals.css` formula's: lightness per step, chroma
 * as a factor of `--brand-c`. The hue and chroma are read out of the file, so
 * repointing the ramp without updating this suite fails here first.
 */

// Spelled out from the vitest root rather than derived from `import.meta.url`,
// which under the jsdom environment is an http URL and not a path.
const GLOBALS = path.join(process.cwd(), "src/app/globals.css");

function readTokens(): { hue: number; chroma: number; successHue: number } {
  const css = readFileSync(GLOBALS, "utf8");
  const hue = /--brand-h:\s*([\d.]+);/.exec(css)?.[1];
  const chroma = /--brand-c:\s*([\d.]+);/.exec(css)?.[1];
  const success = /--color-success:\s*oklch\(58%\s*[\d.]+\s*([\d.]+)\)/.exec(css)?.[1];
  if (hue === undefined || chroma === undefined || success === undefined) {
    throw new Error("brand tokens not found in globals.css");
  }
  return { hue: Number(hue), chroma: Number(chroma), successHue: Number(success) };
}

/** Oklch (L 0-1, C, hue degrees) to sRGB (0-1 per channel). */
function oklchToSrgb(l: number, c: number, hDeg: number): [number, number, number] {
  const h = (hDeg * Math.PI) / 180;
  const a = c * Math.cos(h);
  const b = c * Math.sin(h);
  const l_ = l + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = l - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = l - 0.0894841775 * a - 1.291485548 * b;
  const lr = l_ ** 3;
  const mg = m_ ** 3;
  const sb = s_ ** 3;
  const r = 4.0767416621 * lr - 3.3077115913 * mg + 0.2309699292 * sb;
  const g = -1.2684380046 * lr + 2.6097574011 * mg - 0.3413193965 * sb;
  const bl = -0.0041960863 * lr - 0.7034186147 * mg + 1.707614701 * sb;
  const encode = (v: number) => {
    const clamped = Math.min(1, Math.max(0, v));
    return clamped <= 0.0031308 ? 12.92 * clamped : 1.055 * clamped ** (1 / 2.4) - 0.055;
  };
  return [encode(r), encode(g), encode(bl)];
}

function hexToSrgb(hex: string): [number, number, number] {
  const value = Number.parseInt(hex.slice(1), 16);
  return [((value >> 16) & 0xff) / 255, ((value >> 8) & 0xff) / 255, (value & 0xff) / 255];
}

function luminance([r, g, b]: [number, number, number]): number {
  const linear = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b);
}

function contrast(fg: [number, number, number], bg: [number, number, number]): number {
  const a = luminance(fg);
  const b = luminance(bg);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

describe("the green brand ramp", () => {
  const { hue, chroma, successHue } = readTokens();
  // The ramp's own steps, from the formula in globals.css: brand-700 is the
  // light fill (L 48%, chroma ×0.98), brand-400 the dark fill (L 70%, ×0.8),
  // brand-50 the light subtle tint (L 97%, ×0.06), brand-500 the ring.
  const fill = oklchToSrgb(0.48, chroma * 0.98, hue);
  const darkFill = oklchToSrgb(0.7, chroma * 0.8, hue);
  const darkInk = oklchToSrgb(0.17, 0.03, hue);
  const subtle = oklchToSrgb(0.97, chroma * 0.06, hue);
  const ring = oklchToSrgb(0.62, chroma, hue);
  const white: [number, number, number] = [1, 1, 1];
  const darkCard = hexToSrgb("#29292c");

  it("points the brand at green, saturated enough to read as a colour", () => {
    expect(hue).toBe(155);
    expect(chroma).toBeGreaterThanOrEqual(0.1);
  });

  it("clears 4.5:1 for white text on the light primary fill", () => {
    // Measured 6.09:1. The mockup's #15803d placeholder measured 5.02:1 -
    // it also passes, but the ramp's own step is deeper and is the value.
    expect(contrast(white, fill)).toBeGreaterThanOrEqual(4.5);
  });

  it("clears 4.5:1 for the brand text on its own subtle tint (the active nav row)", () => {
    expect(contrast(fill, subtle)).toBeGreaterThanOrEqual(4.5);
  });

  it("clears 4.5:1 for the dark fill against its dark foreground ink", () => {
    expect(contrast(darkFill, darkInk)).toBeGreaterThanOrEqual(4.5);
  });

  it("keeps the dark fill legible as accent text on the dark card", () => {
    expect(contrast(darkFill, darkCard)).toBeGreaterThanOrEqual(4.5);
  });

  it("keeps the focus ring above 3:1 on white, as a non-text mark", () => {
    expect(contrast(ring, white)).toBeGreaterThanOrEqual(3);
  });

  it("keeps success a different colour from the brand", () => {
    // 23 degrees apart (155 vs 178): "go" and "done" are not the same green.
    expect(Math.abs(successHue - hue)).toBeGreaterThanOrEqual(15);
  });

  it("keeps the success ink legible in both themes", () => {
    expect(contrast(hexToSrgb("#1d6a58"), white)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(hexToSrgb("#9fd8c3"), darkCard)).toBeGreaterThanOrEqual(4.5);
  });
});
