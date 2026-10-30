import { DEFAULT_DESIGN } from "../design";
import type { PreviewResponse, SiteContent } from "../types";

export const SITE: SiteContent = {
  hero: {
    title: "Luna Cafe",
    headline: "Every Cup Tells a Story",
    description: "Small-batch coffee, warm corners and slow mornings.",
    cta_label: "See the menu",
  },
  menu: [
    { id: "espresso", name: "Espresso", price: "90" },
    { id: "americano", name: "Americano", price: "110" },
    { id: "latte", name: "Latte", price: "130" },
  ],
  currency: "TRY",
  design: DEFAULT_DESIGN,
};

export const PREVIEW: PreviewResponse = {
  job_id: "job1",
  published: SITE,
  draft: {
    ...SITE,
    menu: SITE.menu.map((m) => (m.id === "latte" ? { ...m, price: "150" } : m)),
    design: { ...DEFAULT_DESIGN, theme: "dark", hero_alignment: "center" },
  },
  changes: [
    { field: "menu.latte.price", old: "130", new: "150" },
    { field: "design.theme", old: "light", new: "dark" },
  ],
  expires_at: "2026-10-30T12:30:00+00:00",
};
