import { describe, expect, it } from "vitest";

import { DEFAULT_DESIGN, antdTheme, designClasses, resolveDesign } from "./design";
import type { DesignConfig } from "./types";

const ALL: DesignConfig[] = [];
for (const theme of ["light", "dark"] as const)
  for (const palette of ["coffee", "neutral", "forest"] as const)
    for (const hero_alignment of ["left", "center"] as const)
      for (const heading_size of ["normal", "large"] as const)
        for (const card_radius of ["small", "medium", "large"] as const)
          for (const menu_columns of [1, 2, 3] as const)
            ALL.push({ theme, palette, hero_alignment, heading_size, card_radius, menu_columns });

describe("resolveDesign", () => {
  it("keeps valid values", () => {
    const d: DesignConfig = { ...DEFAULT_DESIGN, theme: "dark", menu_columns: 2 };
    expect(resolveDesign(d)).toEqual(d);
  });

  it("falls back to defaults for unknown or hostile values", () => {
    const hostile = {
      theme: "<script>alert(1)</script>",
      palette: "javascript:alert(1)",
      hero_alignment: "center; background:url(//evil)",
      heading_size: 42,
      card_radius: null,
      menu_columns: 99,
      extra: "x",
    };
    expect(resolveDesign(hostile)).toEqual(DEFAULT_DESIGN);
  });

  it("handles non-objects", () => {
    for (const bad of [null, undefined, "dark", 7, []]) {
      expect(resolveDesign(bad)).toEqual(DEFAULT_DESIGN);
    }
  });
});

describe("designClasses", () => {
  it("returns only non-empty class strings for every possible configuration", () => {
    expect(ALL).toHaveLength(2 * 3 * 2 * 2 * 3 * 3);
    for (const d of ALL) {
      const c = designClasses(d);
      for (const value of Object.values(c)) {
        expect(value.length).toBeGreaterThan(0);
        expect(value).not.toMatch(/[<>"'`;{}]/);
      }
    }
  });

  it("maps each option to its explicit class", () => {
    expect(designClasses({ ...DEFAULT_DESIGN, hero_alignment: "center" }).hero).toContain(
      "text-center",
    );
    expect(designClasses({ ...DEFAULT_DESIGN, hero_alignment: "left" }).hero).toContain(
      "text-left",
    );
    expect(designClasses({ ...DEFAULT_DESIGN, heading_size: "large" }).heading).toContain(
      "text-5xl",
    );
    expect(designClasses({ ...DEFAULT_DESIGN, menu_columns: 1 }).grid).toBe("grid-cols-1");
    expect(designClasses({ ...DEFAULT_DESIGN, menu_columns: 3 }).grid).toContain("lg:grid-cols-3");
  });

  it("dark and light use different page classes", () => {
    const light = designClasses({ ...DEFAULT_DESIGN, theme: "light" }).page;
    const dark = designClasses({ ...DEFAULT_DESIGN, theme: "dark" }).page;
    expect(light).not.toEqual(dark);
  });
});

describe("antdTheme", () => {
  it("uses larger card radius for large than small", () => {
    const small = antdTheme({ ...DEFAULT_DESIGN, card_radius: "small" });
    const large = antdTheme({ ...DEFAULT_DESIGN, card_radius: "large" });
    expect(large.components?.Card?.borderRadiusLG).toBeGreaterThan(
      small.components?.Card?.borderRadiusLG ?? 0,
    );
  });

  it("switches algorithm with the theme", () => {
    expect(antdTheme({ ...DEFAULT_DESIGN, theme: "dark" }).algorithm).not.toEqual(
      antdTheme({ ...DEFAULT_DESIGN, theme: "light" }).algorithm,
    );
  });
});
