/** Shared helpers for resource modules: path builders, list normalization, job polling. */
import { api, asList, API_PREFIX, ApiError } from "../client";
import type * as T from "../types";

export const w = (ws: string) => `/workspaces/${encodeURIComponent(ws)}`;
export const e = encodeURIComponent;
export type List<X> = X[] | { items: X[] };
export const list = async <X>(p: Promise<List<X>>): Promise<X[]> => asList(await p);

/* ------------------------------------------------------------------ jobs */

/** Some long-running endpoints return 202 + a job; poll until done and return the final job. */
export async function waitForJob(
  accepted: T.JobAccepted,
  onProgress?: (job: T.Job) => void,
  signal?: AbortSignal,
): Promise<T.Job> {
  let job = accepted.job;
  const url = accepted.poll_url.startsWith(API_PREFIX) ? accepted.poll_url.slice(API_PREFIX.length) : accepted.poll_url;
  let delay = 400;
  while (job.status === "queued" || job.status === "running") {
    await new Promise((r) => setTimeout(r, delay));
    if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
    job = await api.get<T.Job>(url.startsWith("/") ? url : `/${url}`);
    onProgress?.(job);
    delay = Math.min(delay * 1.4, 2000);
  }
  if (job.status === "failed") throw new ApiError(500, job.error || job.message || "Job failed", job);
  return job;
}

export function isJobAccepted(x: unknown): x is T.JobAccepted {
  return !!x && typeof x === "object" && "job" in x && "poll_url" in x;
}

