import { describe, expect, it } from "vitest";
import { forwardRequestHeaders, forwardResponseHeaders, upstreamUrl } from "./proxy";

const incoming = () =>
  new Headers({
    cookie: "aos_session=s; aos_csrf=c",
    "x-csrf-token": "c",
    "content-type": "application/json",
    host: "127.0.0.1:3000",
    connection: "keep-alive",
    // Forged by a remote browser:
    "x-aos-client-addr": "127.0.0.1",
    "x-aos-proxy-secret": "guess",
    "x-aos-web-peer": "127.0.0.1",
    "x-forwarded-for": "127.0.0.1",
    "x-forwarded-host": "localhost",
  });

describe("forwardRequestHeaders", () => {
  it("replaces client-supplied address headers with the recorded peer and the shared secret", () => {
    const h = forwardRequestHeaders(incoming(), { peer: "203.0.113.7", secret: "s3cret", host: "10.0.0.5:3000", proto: "http" });
    expect(h.get("x-aos-client-addr")).toBe("203.0.113.7");
    expect(h.get("x-aos-proxy-secret")).toBe("s3cret");
    expect(h.get("x-forwarded-for")).toBe("203.0.113.7");
    expect(h.get("x-forwarded-host")).toBe("10.0.0.5:3000");
    expect(h.get("x-aos-web-peer")).toBeNull();
    expect(h.get("cookie")).toBe("aos_session=s; aos_csrf=c");
    expect(h.get("x-csrf-token")).toBe("c");
    expect(h.get("host")).toBeNull();
    expect(h.get("connection")).toBeNull();
  });
  it("fails closed when the peer is unknown", () => {
    const h = forwardRequestHeaders(incoming(), { peer: null, secret: "s3cret", host: "localhost:3000", proto: "http" });
    expect(h.get("x-aos-client-addr")).toBe("unknown");
    expect(h.get("x-forwarded-for")).toBeNull();
  });
  it("sends no client-address claim without a secret, and never passes forged ones through", () => {
    const h = forwardRequestHeaders(incoming(), { peer: "127.0.0.1", secret: null, host: "localhost:3000", proto: "http" });
    expect(h.get("x-aos-client-addr")).toBeNull();
    expect(h.get("x-aos-proxy-secret")).toBeNull();
  });
});

describe("forwardResponseHeaders", () => {
  it("keeps every Set-Cookie and drops encoding/length already handled by fetch", () => {
    const up = new Headers();
    up.append("set-cookie", "aos_session=a; HttpOnly");
    up.append("set-cookie", "aos_csrf=b");
    up.set("content-encoding", "gzip");
    up.set("content-length", "10");
    up.set("content-type", "application/json");
    up.set("x-request-id", "r1");
    const out = forwardResponseHeaders(up);
    expect(out.getSetCookie()).toEqual(["aos_session=a; HttpOnly", "aos_csrf=b"]);
    expect(out.get("content-encoding")).toBeNull();
    expect(out.get("content-length")).toBeNull();
    expect(out.get("x-request-id")).toBe("r1");
  });
});

describe("upstreamUrl", () => {
  it("joins the API base, encoded segments and the query", () => {
    expect(upstreamUrl("http://127.0.0.1:8000/", ["v1", "workspaces", "a b"], "?q=1")).toBe("http://127.0.0.1:8000/api/v1/workspaces/a%20b?q=1");
  });
});
