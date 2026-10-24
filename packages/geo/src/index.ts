// Generated data lives in locations.json; rebuild it with `uv run datasites geo build` in pipelines/.
import data from "./locations.json";

export type Level = "world" | "country" | "us-state" | "us-county";

export interface Location {
  id: string;
  level: Level;
  name: string;
  parent?: string;
  /** Countries */
  iso2?: string;
  iso3?: string;
  numeric?: string | null;
  /** US states and counties */
  usps?: string;
  fips?: string;
  kind?: "state" | "district" | "territory";
  /** County internal point (Census INTPTLAT/INTPTLONG) */
  lat?: number;
  lon?: number;
}

export interface Source {
  name: string;
  url: string;
  license: string;
}

export const sources: readonly Source[] = data.sources;
export const locations: readonly Location[] = data.locations as Location[];

const byId = new Map(locations.map((loc) => [loc.id, loc]));
const byParent = new Map<string, Location[]>();
for (const loc of locations) {
  if (loc.parent) byParent.set(loc.parent, [...(byParent.get(loc.parent) ?? []), loc]);
}

export function location(id: string): Location | undefined {
  return byId.get(id);
}

export function isLocationId(id: string): boolean {
  return byId.has(id);
}

export function children(id: string): readonly Location[] {
  return byParent.get(id) ?? [];
}

export function byLevel(level: Level): Location[] {
  return locations.filter((loc) => loc.level === level);
}
