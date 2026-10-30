import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppRoutes } from "./App";
import { DEFAULT_DESIGN } from "./design";
import { PREVIEW, SITE } from "./test/fixtures";

function mockFetch(routes: Record<string, { status: number; body?: unknown }>) {
  const fn = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const hit = routes[url];
    if (!hit) return new Response("{}", { status: 404 });
    return new Response(JSON.stringify(hit.body ?? {}), {
      status: hit.status,
      headers: { "content-type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <AppRoutes />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("public site", () => {
  it("renders hero and menu with prices from the API", async () => {
    mockFetch({ "/api/site": { status: 200, body: { version_id: "v1", content: SITE } } });
    renderAt("/");
    expect(await screen.findByRole("heading", { name: "Every Cup Tells a Story" })).toBeVisible();
    expect(screen.getByText("Luna Cafe")).toBeVisible();
    const menu = screen.getByRole("region", { name: /menu/i });
    expect(within(menu).getByText("Latte")).toBeVisible();
    expect(within(menu).getByText("130 TRY")).toBeVisible();
    expect(within(menu).getAllByRole("listitem")).toHaveLength(3);
  });

  it("applies the design configuration through explicit class mappings", async () => {
    const design = { ...DEFAULT_DESIGN, theme: "dark", hero_alignment: "center", menu_columns: 2 };
    mockFetch({
      "/api/site": { status: 200, body: { version_id: "v1", content: { ...SITE, design } } },
    });
    renderAt("/");
    const site = await screen.findByTestId("site");
    expect(site).toHaveAttribute("data-theme", "dark");
    expect(screen.getByTestId("hero")).toHaveClass("text-center");
    expect(screen.getByTestId("menu-grid")).toHaveClass("sm:grid-cols-2");
    expect(screen.getByTestId("menu-grid")).not.toHaveClass("lg:grid-cols-3");
  });

  it("never renders AI-supplied markup as HTML", async () => {
    const evil = "<img src=x onerror=alert(1)>";
    mockFetch({
      "/api/site": {
        status: 200,
        body: {
          version_id: "v1",
          content: { ...SITE, menu: [{ id: "latte", name: evil, price: "1" }] },
        },
      },
    });
    const { container } = renderAt("/");
    expect(await screen.findByText(evil)).toBeVisible();
    expect(container.querySelector("img")).toBeNull();
  });

  it("shows a friendly error when the API is down", async () => {
    mockFetch({ "/api/site": { status: 503 } });
    renderAt("/");
    expect(await screen.findByText(/could not load/i)).toBeVisible();
  });
});

describe("preview page", () => {
  it("shows changes and lets the owner switch between proposed and current", async () => {
    mockFetch({ "/api/preview/tok123": { status: 200, body: PREVIEW } });
    renderAt("/preview/tok123");

    expect(await screen.findByText(/nothing is live until you approve/i)).toBeVisible();
    expect(screen.getByText("menu.latte.price")).toBeVisible();
    expect(screen.getByText("150 TRY")).toBeVisible(); // proposed by default
    expect(screen.getByTestId("site")).toHaveAttribute("data-theme", "dark");

    await userEvent.click(screen.getByText("Current"));
    await waitFor(() => expect(screen.getByText("130 TRY")).toBeVisible());
    expect(screen.getByTestId("site")).toHaveAttribute("data-theme", "light");
  });

  it("explains unavailable previews without leaking the reason", async () => {
    mockFetch({ "/api/preview/bad": { status: 404 } });
    renderAt("/preview/bad");
    expect(await screen.findByText(/expired or is no longer available/i)).toBeVisible();
  });

  it("asks search engines not to index it", async () => {
    mockFetch({ "/api/preview/tok123": { status: 200, body: PREVIEW } });
    renderAt("/preview/tok123");
    await screen.findByTestId("site");
    expect(document.head.querySelector('meta[name="robots"]')).toHaveAttribute(
      "content",
      "noindex, nofollow",
    );
  });
});

describe("routing", () => {
  it("shows not found for unknown routes", () => {
    mockFetch({});
    renderAt("/nope");
    expect(screen.getByText(/page not found/i)).toBeVisible();
  });
});
