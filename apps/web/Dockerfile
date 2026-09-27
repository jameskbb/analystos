# AnalystOS web (Next.js served by server.mjs). Build from apps/web:
#   docker build -t analystos-web apps/web
# Runtime env: API_URL (where the /api proxy sends requests) and AOS_PROXY_SECRET (shared with the API).
# server.mjs records each request's socket address so the /api proxy can pass the real client to the
# API (X-AOS-Client-Addr + X-AOS-Proxy-Secret); see docs/security.md.
FROM node:24-slim AS deps
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@12.4.1 --activate
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store pnpm install --frozen-lockfile

FROM node:24-slim AS build
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@12.4.1 --activate
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN mkdir -p public && pnpm build

FROM node:24-slim AS prod-deps
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@12.4.1 --activate
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store pnpm install --frozen-lockfile --prod

FROM node:24-slim AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
RUN useradd --system --uid 10001 nextjs
COPY --from=prod-deps --chown=nextjs /app/node_modules ./node_modules
COPY --from=build --chown=nextjs /app/.next ./.next
COPY --from=build --chown=nextjs /app/public ./public
COPY --chown=nextjs package.json next.config.ts server.mjs ./
USER nextjs
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD node -e "fetch('http://127.0.0.1:3000/').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
# Inside the container the server listens on all interfaces of the container network; compose
# publishes the port on the host's loopback only.
CMD ["node", "server.mjs"]
