import type { PreviewResponse, SiteResponse } from "./types";

export class ApiError extends Error {
  constructor(public readonly status: number) {
    super(`HTTP ${status}`);
  }
}

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { headers: { accept: "application/json" } });
  if (!response.ok) throw new ApiError(response.status);
  return (await response.json()) as T;
}

export const fetchSite = (): Promise<SiteResponse> => getJson("/api/site");

export const fetchPreview = (token: string): Promise<PreviewResponse> =>
  getJson(`/api/preview/${encodeURIComponent(token)}`);
