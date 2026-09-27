/**
 * Minimal fetch wrapper for the AnalystOS API.
 *
 * - All requests go to `/api/v1/...` on the same origin; Next rewrites proxy them to the FastAPI server.
 * - Session auth uses the HttpOnly `aos_session` cookie. State-changing requests echo the CSRF token
 *   (from the readable `aos_csrf` cookie or from the last auth response) in `X-CSRF-Token`.
 * - Errors are normalized into `ApiError` with the FastAPI `detail` message.
 */

export const API_PREFIX = "/api/v1";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly detail: unknown;
  /** The error body's structured `errors` (e.g. `{block_ids}` for `unreviewed_blocks`, `{fallback}` for PDF). */
  readonly errors: unknown;

  constructor(status: number, message: string, detail?: unknown, code?: string | null, errors?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.code = code ?? null;
    this.errors = errors ?? null;
  }

  get isUnauthorized(): boolean {
    return this.status === 401;
  }
  get isForbidden(): boolean {
    return this.status === 403;
  }
  get isNotFound(): boolean {
    return this.status === 404;
  }
}

let csrfToken: string | null = null;
export function setCsrfToken(token: string | null | undefined): void {
  csrfToken = token ?? null;
}

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.split("; ").find((c) => c.startsWith(`${name}=`));
  return match ? decodeURIComponent(match.slice(name.length + 1)) : null;
}

type Unauthorized = () => void;
let onUnauthorized: Unauthorized | null = null;
export function setUnauthorizedHandler(handler: Unauthorized | null): void {
  onUnauthorized = handler;
}

export type QueryParams = Record<string, string | number | boolean | null | undefined | (string | number)[]>;

export function buildQuery(params?: QueryParams): string {
  if (!params) return "";
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((item) => usp.append(k, String(item)));
    else usp.set(k, String(v));
  }
  const s = usp.toString();
  return s ? `?${s}` : "";
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  query?: QueryParams;
  signal?: AbortSignal;
  /** Return the raw Response (downloads). */
  raw?: boolean;
  headers?: Record<string, string>;
}

export function errorMessage(detail: unknown, fallback: string): string {
  if (!detail) return fallback;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((d) => {
        if (d && typeof d === "object" && "msg" in d) {
          const loc = Array.isArray((d as { loc?: unknown[] }).loc)
            ? (d as { loc: unknown[] }).loc.filter((x) => x !== "body").join(".")
            : "";
          return loc ? `${loc}: ${(d as { msg: string }).msg}` : (d as { msg: string }).msg;
        }
        return typeof d === "string" ? d : JSON.stringify(d);
      })
      .filter(Boolean);
    return parts.join("; ") || fallback;
  }
  if (typeof detail === "object") {
    const o = detail as Record<string, unknown>;
    if (typeof o.message === "string") return o.message;
    if (typeof o.detail === "string") return o.detail;
  }
  return fallback;
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const method = opts.method ?? (opts.body !== undefined ? "POST" : "GET");
  const headers: Record<string, string> = { Accept: "application/json", ...opts.headers };
  let body: BodyInit | undefined;
  if (opts.body instanceof FormData) {
    body = opts.body;
  } else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  if (method !== "GET") {
    const token = readCookie("aos_csrf") ?? csrfToken;
    if (token) headers["X-CSRF-Token"] = token;
  }

  let res: Response;
  try {
    res = await fetch(`${API_PREFIX}${path}${buildQuery(opts.query)}`, {
      method,
      headers,
      body,
      credentials: "same-origin",
      signal: opts.signal,
      cache: "no-store",
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(0, "Cannot reach the AnalystOS API. Is the server running?", String(err), "network");
  }

  if (!res.ok) {
    let detail: unknown = null;
    let code: string | null = null;
    let errors: unknown = null;
    try {
      const data = (await res.json()) as {
        detail?: unknown;
        code?: string;
        errors?: unknown;
        error?: { message?: string; code?: string };
      };
      detail = data.detail ?? data.error ?? data;
      code = data.code ?? data.error?.code ?? null;
      errors = data.errors ?? null;
    } catch {
      detail = res.statusText;
    }
    if (res.status === 401 && onUnauthorized) onUnauthorized();
    const fallback =
      res.status === 502 || res.status === 503 || res.status === 504
        ? "The AnalystOS API is unavailable."
        : `Request failed (${res.status})`;
    throw new ApiError(res.status, errorMessage(detail, fallback), detail, code, errors);
  }

  if (opts.raw) return res as unknown as T;
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    return text as unknown as T;
  }
}

/** Normalizes list endpoints that return either an array or `{items: [...]}`. */
export function asList<T>(data: T[] | { items: T[] } | null | undefined): T[] {
  if (!data) return [];
  if (Array.isArray(data)) return data;
  if (Array.isArray((data as { items?: T[] }).items)) return (data as { items: T[] }).items;
  return [];
}

export const api = {
  get: <T>(path: string, query?: QueryParams, signal?: AbortSignal) => request<T>(path, { query, signal }),
  post: <T>(path: string, body?: unknown, query?: QueryParams) =>
    request<T>(path, { method: "POST", body: body ?? {}, query }),
  put: <T>(path: string, body?: unknown) => request<T>(path, { method: "PUT", body: body ?? {} }),
  patch: <T>(path: string, body?: unknown) => request<T>(path, { method: "PATCH", body: body ?? {} }),
  del: <T = void>(path: string, query?: QueryParams) => request<T>(path, { method: "DELETE", query }),
  upload: <T>(path: string, form: FormData, query?: QueryParams) =>
    request<T>(path, { method: "POST", body: form, query }),
};

/** Triggers a browser download of a same-origin URL or Blob. */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function downloadFromApi(path: string, fallbackName: string, body?: unknown): Promise<void> {
  const res = await request<Response>(path, { method: body === undefined ? "GET" : "POST", body, raw: true });
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
  const name = match ? decodeURIComponent(match[1]) : fallbackName;
  const ctype = res.headers.get("Content-Type") ?? "";
  if (ctype.includes("application/json")) {
    // Export endpoints may return a stored-file record with a download URL.
    const data = (await res.json()) as { download_url?: string; filename?: string };
    if (data.download_url) {
      const file = await fetch(data.download_url, { credentials: "same-origin" });
      if (!file.ok) throw new ApiError(file.status, "Export download failed");
      downloadBlob(await file.blob(), data.filename ?? name);
      return;
    }
    downloadBlob(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }), name);
    return;
  }
  downloadBlob(await res.blob(), name);
}
