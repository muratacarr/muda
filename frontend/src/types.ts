export type Theme = "light" | "dark";
export type Palette = "coffee" | "neutral" | "forest";
export type HeroAlignment = "left" | "center";
export type HeadingSize = "normal" | "large";
export type CardRadius = "small" | "medium" | "large";
export type MenuColumns = 1 | 2 | 3;

export interface DesignConfig {
  theme: Theme;
  palette: Palette;
  hero_alignment: HeroAlignment;
  heading_size: HeadingSize;
  card_radius: CardRadius;
  menu_columns: MenuColumns;
}

export interface MenuItem {
  id: string;
  name: string;
  price: string;
}

export interface SiteContent {
  hero: { title: string; headline: string; description: string; cta_label: string };
  menu: MenuItem[];
  currency: string;
  design: DesignConfig;
}

export interface SiteResponse {
  version_id: string;
  content: SiteContent;
}

export interface Change {
  field: string;
  old: string;
  new: string;
}

export interface PreviewResponse {
  job_id: string;
  published: SiteContent;
  draft: SiteContent;
  changes: Change[];
  expires_at: string;
}
