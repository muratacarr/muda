// The AI never produces CSS. It picks values from closed vocabularies; this module is the only
// place those values become styles, through explicit literal class names and theme tokens.
import { theme as antTheme, type ThemeConfig } from "antd";

import type {
  CardRadius,
  DesignConfig,
  HeadingSize,
  HeroAlignment,
  MenuColumns,
  Palette,
  Theme,
} from "./types";

export const DEFAULT_DESIGN: DesignConfig = {
  theme: "light",
  palette: "coffee",
  hero_alignment: "left",
  heading_size: "normal",
  card_radius: "medium",
  menu_columns: 3,
};

const THEMES: readonly Theme[] = ["light", "dark"];
const PALETTES: readonly Palette[] = ["coffee", "neutral", "forest"];
const ALIGNMENTS: readonly HeroAlignment[] = ["left", "center"];
const HEADING_SIZES: readonly HeadingSize[] = ["normal", "large"];
const RADII: readonly CardRadius[] = ["small", "medium", "large"];
const COLUMNS: readonly MenuColumns[] = [1, 2, 3];

function pick<T>(allowed: readonly T[], value: unknown, fallback: T): T {
  return allowed.find((candidate) => candidate === value) ?? fallback;
}

/** Defense in depth: anything outside the vocabulary silently becomes the default. */
export function resolveDesign(raw: unknown): DesignConfig {
  const r = (typeof raw === "object" && raw !== null ? raw : {}) as Record<string, unknown>;
  return {
    theme: pick(THEMES, r.theme, DEFAULT_DESIGN.theme),
    palette: pick(PALETTES, r.palette, DEFAULT_DESIGN.palette),
    hero_alignment: pick(ALIGNMENTS, r.hero_alignment, DEFAULT_DESIGN.hero_alignment),
    heading_size: pick(HEADING_SIZES, r.heading_size, DEFAULT_DESIGN.heading_size),
    card_radius: pick(RADII, r.card_radius, DEFAULT_DESIGN.card_radius),
    menu_columns: pick(COLUMNS, r.menu_columns, DEFAULT_DESIGN.menu_columns),
  };
}

// Full literal class names so the Tailwind scanner sees them. Never build these dynamically.
const PAGE: Record<Theme, Record<Palette, { page: string; muted: string }>> = {
  light: {
    coffee: { page: "bg-amber-50 text-stone-900", muted: "text-stone-700" },
    neutral: { page: "bg-slate-50 text-slate-900", muted: "text-slate-700" },
    forest: { page: "bg-emerald-50 text-emerald-950", muted: "text-emerald-900" },
  },
  dark: {
    coffee: { page: "bg-stone-900 text-stone-100", muted: "text-stone-300" },
    neutral: { page: "bg-slate-900 text-slate-100", muted: "text-slate-300" },
    forest: { page: "bg-emerald-950 text-emerald-50", muted: "text-emerald-100" },
  },
};

const HERO: Record<HeroAlignment, string> = {
  left: "text-left items-start",
  center: "text-center items-center",
};

const HEADING: Record<HeadingSize, string> = {
  normal: "text-4xl sm:text-5xl",
  large: "text-5xl sm:text-7xl",
};

const GRID: Record<MenuColumns, string> = {
  1: "grid-cols-1",
  2: "grid-cols-1 sm:grid-cols-2",
  3: "grid-cols-1 sm:grid-cols-2 lg:grid-cols-3",
};

export interface DesignClasses {
  page: string;
  muted: string;
  hero: string;
  heading: string;
  grid: string;
}

export function designClasses(d: DesignConfig): DesignClasses {
  const { page, muted } = PAGE[d.theme][d.palette];
  return {
    page,
    muted,
    hero: HERO[d.hero_alignment],
    heading: HEADING[d.heading_size],
    grid: GRID[d.menu_columns],
  };
}

const PRIMARY: Record<Theme, Record<Palette, { color: string; onColor: string }>> = {
  light: {
    coffee: { color: "#92400e", onColor: "#ffffff" },
    neutral: { color: "#334155", onColor: "#ffffff" },
    forest: { color: "#047857", onColor: "#ffffff" },
  },
  dark: {
    coffee: { color: "#fbbf24", onColor: "#1c1917" },
    neutral: { color: "#e2e8f0", onColor: "#0f172a" },
    forest: { color: "#34d399", onColor: "#022c22" },
  },
};

const CARD_RADIUS_PX: Record<CardRadius, number> = { small: 6, medium: 16, large: 28 };

export function antdTheme(d: DesignConfig): ThemeConfig {
  const { color, onColor } = PRIMARY[d.theme][d.palette];
  return {
    algorithm: d.theme === "dark" ? antTheme.darkAlgorithm : antTheme.defaultAlgorithm,
    token: { colorPrimary: color, borderRadius: 8 },
    components: {
      Card: { borderRadiusLG: CARD_RADIUS_PX[d.card_radius] },
      Button: { primaryColor: onColor },
    },
  };
}
