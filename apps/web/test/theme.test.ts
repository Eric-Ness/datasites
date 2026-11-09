import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { sites } from "@datasites/registry";
import { ACCENT, DARK, SHARED, LIGHT, accentPalette, contrast, ensureContrast, faviconSvg, hexToRgb, luminance, markColors, themeCss } from "../src/lib/theme";

describe("accents", () => {
  it("match the coming-soon page exactly", () => {
    const page = readFileSync(fileURLToPath(new URL("../../coming-soon/src/page.ts", import.meta.url)), "utf8");
    const block = /const ACCENT[^{]*\{([^}]*)\}/.exec(page)?.[1] ?? "";
    const theirs = Object.fromEntries([...block.matchAll(/(\w+):\s*"(#[0-9a-f]{6})"/gi)].map((m) => [m[1], m[2]]));
    expect(theirs).toEqual(ACCENT);
  });

  it("exist for every registry site", () => {
    for (const site of sites) expect(ACCENT[site.id], site.id).toBeDefined();
  });

  it("meet WCAG AA contrast in both themes for every site", () => {
    for (const id of Object.keys(ACCENT)) {
      const p = accentPalette(id);
      for (const bg of [LIGHT.bg, LIGHT.card, LIGHT.code]) expect(contrast(p.light.text, bg), `${id} light text`).toBeGreaterThanOrEqual(4.5);
      for (const bg of [DARK.bg, DARK.card, DARK.code]) expect(contrast(p.dark.text, bg), `${id} dark text`).toBeGreaterThanOrEqual(4.5);
      expect(contrast(p.light.fill, p.light.onFill), `${id} light button`).toBeGreaterThanOrEqual(4.5);
      expect(contrast(p.dark.fill, p.dark.onFill), `${id} dark button`).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("stay close to the brand hue", () => {
    const p = accentPalette("climatedata");
    const [r, g, b] = hexToRgb(p.light.text);
    expect(r).toBeGreaterThan(g);
    expect(r).toBeGreaterThan(b);
  });
});

describe("contrast helpers", () => {
  it("compute WCAG ratios", () => {
    expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrast("#777777", "#777777")).toBeCloseTo(1, 5);
  });

  it("ensureContrast leaves good colors alone and fixes weak ones", () => {
    expect(ensureContrast("#000000", ["#ffffff"], 4.5, "#000000")).toBe("#000000");
    const fixed = ensureContrast("#b5761f", ["#ffffff"], 4.5, "#000000");
    expect(contrast(fixed, "#ffffff")).toBeGreaterThanOrEqual(4.5);
  });
});

describe("theme CSS and favicon", () => {
  it("defines one light theme with visualizedata.org's shared tokens", () => {
    const css = themeCss("waterdata");
    expect(css).toContain("--accent:");
    expect(css).toContain(`--bg:${LIGHT.bg}`);
    expect(css).toContain(`--link:${SHARED.link}`);
    expect(css).toContain("color-scheme:light");
    expect(css).not.toContain("prefers-color-scheme:dark");
  });

  it("draws the five-bar mark in the brand accent, from a pale tint to a deep shade", () => {
    const svg = faviconSvg("airdata");
    expect(svg).toContain(`fill="${ACCENT.airdata}"`);
    expect(svg.match(/<rect /g)).toHaveLength(5);
    const colors = markColors("airdata");
    expect(colors[2]).toBe(ACCENT.airdata);
    expect(luminance(colors[0]!)).toBeGreaterThan(luminance(colors[4]!));
  });
});
