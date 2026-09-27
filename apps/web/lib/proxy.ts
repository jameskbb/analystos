/**
 * Server-side `/api` proxy logic (used by app/api/[...path]/route.ts). Pure functions, unit-tested.
 *
 * The API decides whether a request may use single-user local mode from the TCP peer. Behind this
 * proxy every request's peer is the web server itself, so the proxy tells the API who the real
 * client is: `X-AOS-Client-Addr` (the socket address recorded by server.mjs) authenticated by
 * `X-AOS-Proxy-Secret` (= AOS_PROXY_SECRET, shared with the API). Client-supplied copies of these
 * headers and of the internal peer header are always dropped, so a browser cannot claim to be local.
 */

/** Set by server.mjs from `req.socket.remoteAddress`, overwriting anything the client sent. */
export const PEER_HEADER = "x-aos-web-peer";
export const CLIENT_ADDR_HEADER = "x-aos-client-addr";
export const PROXY_SECRET_HEADER = "x-aos-proxy-secret";

const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "host",
  "content-length",
]);

/** Never forwarded from the browser: the proxy alone may assert the client address. */
const SPOOFABLE = new Set([PEER_HEADER, CLIENT_ADDR_HEADER, PROXY_SECRET_HEADER, "x-forwarded-for", "x-real-ip", "forwarded"]);

export interface ForwardOptions {
  /** Real client address recorded by server.mjs, or null when the app runs without it. */
  peer: string | null;
  /** AOS_PROXY_SECRET; without it the client address is not sent (see docs/security.md). */
  secret: string | null;
  /** Host the browser used, passed on as X-Forwarded-Host. */
  host: string | null;
  proto: string;
}

export function forwardRequestHeaders(incoming: Headers, opts: ForwardOptions): Headers {
  const out = new Headers();
  incoming.forEach((value, key) => {
    const k = key.toLowerCase();
    if (HOP_BY_HOP.has(k) || SPOOFABLE.has(k) || k.startsWith("x-forwarded-")) return;
    out.append(key, value);
  });
  if (opts.host) out.set("x-forwarded-host", opts.host);
  out.set("x-forwarded-proto", opts.proto);
  if (opts.secret) {
    // Unknown peer ("unknown") is not loopback, so the API refuses local mode: fail closed.
    out.set(CLIENT_ADDR_HEADER, opts.peer || "unknown");
    out.set(PROXY_SECRET_HEADER, opts.secret);
  }
  if (opts.peer) out.set("x-forwarded-for", opts.peer);
  return out;
}

/** Response headers to pass back; fetch() already decoded the body, so length/encoding are dropped. */
export function forwardResponseHeaders(upstream: Headers): Headers {
  const out = new Headers();
  upstream.forEach((value, key) => {
    const k = key.toLowerCase();
    if (HOP_BY_HOP.has(k) || k === "content-encoding" || k === "set-cookie") return;
    out.append(key, value);
  });
  for (const c of upstream.getSetCookie()) out.append("set-cookie", c);
  return out;
}

/** `${API_URL}/api/<path>?<query>` for a proxied request path. */
export function upstreamUrl(apiUrl: string, segments: string[], search: string): string {
  const base = apiUrl.replace(/\/+$/, "");
  return `${base}/api/${segments.map(encodeURIComponent).join("/")}${search}`;
}
