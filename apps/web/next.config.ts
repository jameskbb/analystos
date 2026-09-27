import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Separate build output lets a verification build run while `next dev` uses .next.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  poweredByHeader: false,
  // /api/* is proxied at runtime by app/api/[...path]/route.ts (API_URL is read per request, not
  // baked in at build time) so the real client address can be passed to the API; see lib/proxy.ts.
};

export default nextConfig;
