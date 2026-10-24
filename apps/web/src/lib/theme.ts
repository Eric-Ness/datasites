// Colors. Each site keeps the accent hue of its coming-soon page; the text and fill variants are
// derived from it so that every use meets WCAG AA contrast in both the light and dark themes.

/** Copied from apps/coming-soon/src/page.ts so the real site matches its placeholder. */
export const ACCENT: Record<string, string> = {
  airdata: "#2f7fd1",
  carbondata: "#3b8a5a",
  climatedata: "#c4532d",
  economicdata: "#6a4fc4",
  electiondata: "#b23a6b",
  satellitedata: "#2b6f8f",
  visualizedata: "#b5761f",
  waterdata: "#1f8a9a",
  wildfiredata: "#c2362b",
};

export const DEFAULT_ACCENT = "#c4532d";

/** Surfaces, matching the coming-soon page. "Lowest contrast" surfaces are the ones accent text sits on. */
export const LIGHT = {
  bg: "#fbfaf7",
  fg: "#1d1d1b",
  muted: "#5d5b55",
  line: "#dedbd2",
  card: "#ffffff",
  code: "#f3f1ec",
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

/** The per-site custom properties, for a <style> tag in the page head. */
export function themeCss(siteId: string): string {
  const p = accentPalette(siteId);
  // A neutral second line color that stays distinguishable from every accent (>= 3:1 on the background).
  const lightChart2 = ensureContrast("#5b6b7a", [LIGHT.bg, LIGHT.card], 3, "#000000");
  const darkChart2 = ensureContrast("#9aa8b5", [DARK.bg, DARK.card], 3, "#ffffff");
  return (
    `:root{color-scheme:light dark;--brand:${p.brand};${vars(LIGHT, p.light, lightChart2)}}` +
    `@media (prefers-color-scheme:dark){:root{${vars(DARK, p.dark, darkChart2)}}}`
  );
}

/** The favicon: the coming-soon bar-chart mark in the site's brand accent. */
export function faviconSvg(siteId: string): string {
  const accent = ACCENT[siteId] ?? DEFAULT_ACCENT;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" fill="${accent}"/><path d="M8 22V14M14 22V9M20 22V17M26 22V12" stroke="#fff" stroke-width="3" stroke-linecap="round"/></svg>`;
}
