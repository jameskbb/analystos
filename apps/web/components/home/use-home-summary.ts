"use client";
import { useQuery } from "@tanstack/react-query";
import { ApiError } from "@/lib/api/client";
import { dashboards, datasets, findings, investigations, quality, workspaces } from "@/lib/api/endpoints";
import { qk } from "@/lib/api/keys";
import type { HomeSummary } from "@/lib/api/types";

/** Fills every list so the UI never has to guard against missing sections. */
export function normalizeHome(raw: Partial<HomeSummary> | null | undefined): HomeSummary {
  return {
    workspace: raw?.workspace,
    counts: raw?.counts ?? {},
    datasets: raw?.datasets ?? [],
    recent_investigations: raw?.recent_investigations ?? [],
    recent_findings: raw?.recent_findings ?? [],
    quality_failures: raw?.quality_failures ?? [],
    dashboards: raw?.dashboards ?? [],
    pending_relationships: raw?.pending_relationships ?? 0,
    running_jobs: raw?.running_jobs ?? [],
    changes: raw?.changes ?? [],
  };
}

/**
 * Composes the home summary from the individual list endpoints. Used only when the server does not
 * expose `GET /workspaces/{ws}/home` (older API builds); metric changes are then unavailable.
 */
/** An endpoint the server does not provide (404) contributes an empty section; other errors still surface. */
async function orEmpty<T>(p: Promise<T[]>): Promise<T[]> {
  try {
    return await p;
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return [];
    throw e;
  }
}

async function composeHome(ws: string): Promise<HomeSummary> {
  const [ds, inv, fin, rules, dash] = await Promise.all([
    datasets.list(ws),
    orEmpty(investigations.list(ws, { limit: 6 })),
    orEmpty(findings.list(ws, { limit: 6 })),
    orEmpty(quality.rules(ws, {})),
    orEmpty(dashboards.list(ws)),
  ]);
  return normalizeHome({
    datasets: ds.map((d) => ({
      id: d.id,
      name: d.name,
      table_name: d.table_name,
      row_count: d.row_count,
      updated_at: d.updated_at ?? d.created_at,
      profile_status: d.profile_status,
      issue_count: d.issue_count ?? null,
    })),
    recent_investigations: inv.slice(0, 6),
    recent_findings: fin.slice(0, 6),
    quality_failures: rules
      .filter((r) => r.last_passed === false)
      .map((r) => ({ id: r.id, name: r.name, table_name: r.table_name, severity: r.severity, dataset_id: r.dataset_id, last_run_at: r.last_run_at })),
    dashboards: dash,
    changes: [],
  });
}

export function useHomeSummary(ws: string) {
  return useQuery({
    queryKey: qk.home(ws),
    queryFn: async () => {
      try {
        return normalizeHome(await workspaces.home(ws));
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) return composeHome(ws);
        throw e;
      }
    },
  });
}
