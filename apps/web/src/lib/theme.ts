// Colors. The sites share visualizedata.org's palette: a white page, blue-black ink and one link blue, in one
// light theme. Each site keeps the accent hue of its coming-soon page for its own mark and its charts; the text
// and fill variants are derived from it so that every use meets WCAG AA contrast.

/** Copied from apps/coming-soon/src/page.ts so the real site matches its placeholder. */
export const ACCENT: Record<string, string> = {
  airdata: "#2f7fd1",
  carbondata: "#3b8a5a",
  climatedata: "#c4532d",
  economicdata: "#6a4fc4",
  electiondata: "#b23a6b",
  healthdata: "#c0577f",
  housingdata: "#6b7a1f",
  justicedata: "#4a5a8c",
  satellitedata: "#2b6f8f",
  visualizedata: "#b5761f",
  waterdata: "#1f8a9a",
  wildfiredata: "#c2362b",
};

export const DEFAULT_ACCENT = "#c4532d";

/** Surfaces, matching visualizedata.org's tokens. "Lowest contrast" surfaces are the ones accent text sits on. */
export const LIGHT = {
  bg: "#ffffff",
  fg: "#1e2833",
  muted: "#4f5b68",
  line: "#dfe3e8",
  card: "#ffffff",
  code: "#f5f6f8",
} as const;

/** visualizedata.org's shared tokens: the same on every site in the collection. */
export const SHARED = {
  link: "#23588c",
  linkHover: "#2f5f8e",
  faint: "#76808c",
  axis: "#c5cbd3",
  grid: "#eef0f3",
  tint: "#f5f6f8",
} as const;

export const DARK = {
  bg: "#141413",
  fg: "#ecebe6",
  muted: "#a3a19a",
  line: "#33322f",
  card: "#1c1c1a",
  code: "#21211f",
} as const;

type Rgb = [number, number, number];

export function hexToRgb(hex: string): Rgb {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) throw new Error(`not a #rrggbb color: ${hex}`);
  const n = Number.parseInt(m[1]!, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export function rgbToHex([r, g, b]: Rgb): string {
  return `#${[r, g, b].map((c) => Math.round(Math.min(255, Math.max(0, c))).toString(16).padStart(2, "0")).join("")}`;
}

/** WCAG 2 relative luminance. */
export function luminance(hex: string): number {
  const [r, g, b] = hexToRgb(hex).map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  }) as Rgb;
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

export function mix(a: string, b: string, t: number): string {
  const x = hexToRgb(a);
  const y = hexToRgb(b);
  return rgbToHex([0, 1, 2].map((i) => x[i]! + (y[i]! - x[i]!) * t) as Rgb);
}

/** Moves `color` toward `toward` in small steps until it reaches `ratio` against every background. */
export function ensureContrast(color: string, backgrounds: readonly string[], ratio: number, toward: string): string {
  for (let step = 0; step <= 100; step++) {
    const candidate = mix(color, toward, step / 100);
    if (backgrounds.every((bg) => contrast(candidate, bg) >= ratio)) return candidate;
  }
  return toward;
}

export interface ThemeAccent {
  /** Links, eyebrow text, focus rings and chart lines (>= 4.5:1 on every surface). */
  text: string;
  /** Button and badge fills (>= 4.5:1 against `onFill`). */
  fill: string;
  onFill: string;
  /** A faint tint for highlighted rows and selected tabs. */
  tint: string;
}

export interface AccentPalette {
  brand: string;
  light: ThemeAccent;
  dark: ThemeAccent;
}

export function accentPalette(siteId: string): AccentPalette {
  const brand = ACCENT[siteId] ?? DEFAULT_ACCENT;
  const lightSurfaces = [LIGHT.bg, LIGHT.card, LIGHT.code];
  const darkSurfaces = [DARK.bg, DARK.card, DARK.code];
  const lightFill = ensureContrast(brand, ["#ffffff"], 4.5, "#000000");
  const darkText = ensureContrast(brand, darkSurfaces, 4.5, "#ffffff");
  return {
    brand,
    light: {
      text: ensureContrast(brand, lightSurfaces, 4.5, "#000000"),
      fill: lightFill,
      onFill: "#ffffff",
      tint: mix(brand, LIGHT.card, 0.9),
    },
    dark: {
      text: darkText,
      fill: ensureContrast(brand, [DARK.bg], 4.5, "#ffffff"),
      onFill: DARK.bg,
      tint: mix(brand, DARK.card, 0.82),
    },
  };
}

function vars(surface: typeof LIGHT | typeof DARK, accent: ThemeAccent, chart2: string): string {
  return [
    `--bg:${surface.bg}`,
    `--fg:${surface.fg}`,
    `--muted:${surface.muted}`,
    `--line:${surface.line}`,
    `--card:${surface.card}`,
    `--code:${surface.code}`,
    `--accent:${accent.text}`,
    `--accent-fill:${accent.fill}`,
    `--on-accent:${accent.onFill}`,
    `--accent-tint:${accent.tint}`,
    `--chart-2:${chart2}`,
  ].join(";");
}

/** The per-site custom properties, for a <style> tag in the page head. One light theme, as on visualizedata.org. */
export function themeCss(siteId: string): string {
  const p = accentPalette(siteId);
  // A neutral second line color that stays distinguishable from every accent (>= 3:1 on the background).
  const lightChart2 = ensureContrast("#5b6b7a", [LIGHT.bg, LIGHT.card], 3, "#000000");
  const shared = [
    `--link:${SHARED.link}`,
    `--link-hover:${SHARED.linkHover}`,
    `--faint:${SHARED.faint}`,
    `--axis:${SHARED.axis}`,
    `--grid:${SHARED.grid}`,
    `--tint:${SHARED.tint}`,
  ].join(";");
  return `:root{color-scheme:light;--brand:${p.brand};${vars(LIGHT, p.light, lightChart2)};${shared}}`;
}

/**
 * The site mark: visualizedata.org's five bars, drawn in this site's accent from a pale tint to a deep shade, so
 * the sites in the collection share one shape and differ only in hue. Also the favicon.
 */
export function markColors(siteId: string): string[] {
  const accent = ACCENT[siteId] ?? DEFAULT_ACCENT;
  return [mix(accent, "#ffffff", 0.72), mix(accent, "#ffffff", 0.45), accent, mix(accent, "#000000", 0.2), mix(accent, "#000000", 0.4)];
}

export function faviconSvg(siteId: string): string {
  const bars = markColors(siteId)
    .map((c, i) => `<rect x="${i * 5.5}" width="4" height="20" fill="${c}"/>`)
    .join("");
  return `<svg xmlns="http://www.w3.org/2000/svg" width="26" height="20" viewBox="0 0 26 20">${bars}</svg>`;
}
