// Build-time facts from packages/geo: how many places each level has, and whom to credit for the names.

import { byLevel, sources, type Level } from "@datasites/geo";

const LEVELS: Level[] = ["world", "country", "us-state", "us-county"];

export const levelCounts: Record<string, number> = Object.fromEntries(LEVELS.map((level) => [level, byLevel(level).length]));

export const geoSources = sources;
