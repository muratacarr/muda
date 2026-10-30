import { Button, Card, ConfigProvider } from "antd";

import { antdTheme, designClasses, resolveDesign } from "../design";
import type { SiteContent } from "../types";

/** Renders a site snapshot. Used for the live page and for both sides of a preview. */
export function Site({ content }: { content: SiteContent }) {
  const design = resolveDesign(content.design);
  const cls = designClasses(design);
  return (
    <ConfigProvider theme={antdTheme(design)}>
      <div
        data-testid="site"
        data-theme={design.theme}
        className={`min-h-screen transition-colors ${cls.page}`}
      >
        <header
          data-testid="hero"
          className={`mx-auto flex max-w-5xl flex-col gap-6 px-6 py-20 ${cls.hero}`}
        >
          <p className={`text-sm font-semibold uppercase tracking-widest ${cls.muted}`}>
            {content.hero.title}
          </p>
          <h1 className={`font-bold leading-tight ${cls.heading}`}>{content.hero.headline}</h1>
          <p className={`max-w-xl text-lg ${cls.muted}`}>{content.hero.description}</p>
          <Button type="primary" size="large" href="#menu">
            {content.hero.cta_label}
          </Button>
        </header>

        <section id="menu" aria-label="Menu" className="mx-auto max-w-5xl px-6 pb-24">
          <h2 className="mb-8 text-2xl font-semibold">Menu</h2>
          <ul data-testid="menu-grid" className={`grid list-none gap-6 p-0 ${cls.grid}`}>
            {content.menu.map((item) => (
              <li key={item.id}>
                <Card variant="outlined" className="h-full">
                  <div className="flex items-baseline justify-between gap-4">
                    <span className="text-lg font-medium">{item.name}</span>
                    <span className="font-semibold">{`${item.price} ${content.currency}`}</span>
                  </div>
                </Card>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </ConfigProvider>
  );
}
