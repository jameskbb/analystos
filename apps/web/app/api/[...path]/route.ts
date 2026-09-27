/**
 * `/api/*` proxy to the AnalystOS API (replaces a Next.js rewrite, which cannot add per-request
 * headers). Adds the real client address and the shared proxy secret so the API can tell a local
 * browser from a remote one; see lib/proxy.ts and docs/security.md.
 */
import { forwardRequestHeaders, forwardResponseHeaders, PEER_HEADER, upstreamUrl } from "@/lib/proxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const API_URL = process.env.API_URL ?? "http://127.0.0.1:8000";
const SECRET = process.env.AOS_PROXY_SECRET || null;

async function proxy(req: Request, ctx: { params: Promise<{ path: string[] }> }): Promise<Response> {
  const { path } = await ctx.params;
  const url = new URL(req.url);
  const headers = forwardRequestHeaders(req.headers, {
    peer: req.headers.get(PEER_HEADER),
    secret: SECRET,
    host: req.headers.get("host"),
    proto: url.protocol.replace(":", ""),
  });
  const hasBody = req.method !== "GET" && req.method !== "HEAD";
  let upstream: Response;
  try {
    upstream = await fetch(upstreamUrl(API_URL, path, url.search), {
      method: req.method,
      headers,
      body: hasBody ? req.body : undefined,
      redirect: "manual",
      cache: "no-store",
      // Stream request bodies (uploads) instead of buffering them.
      ...(hasBody ? { duplex: "half" } : {}),
    } as RequestInit);
  } catch {
    return Response.json(
      { detail: "The AnalystOS API is unavailable.", code: "api_unavailable", request_id: null, errors: null },
      { status: 502 },
    );
  }
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: forwardResponseHeaders(upstream.headers),
  });
}

export { proxy as GET, proxy as POST, proxy as PUT, proxy as PATCH, proxy as DELETE, proxy as HEAD, proxy as OPTIONS };
