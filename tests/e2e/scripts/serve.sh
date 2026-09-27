#!/usr/bin/env bash
# Boot the AnalystOS API and web app for the E2E suite on dedicated ports with a throwaway
# metadata DB and data directory. Playwright starts this script (see playwright.config.ts);
# it can also be run by hand: `pnpm --dir tests/e2e serve`.
#
# Env:
#   E2E_API_PORT (18765)  E2E_WEB_PORT (13765)
#   E2E_WEB_MODE  dev (default): `next dev` in apps/web; routes are compiled up front by a warm-up
#                 build (CI): `next build` in apps/web into .next-e2e, then serve it with
#                 server.mjs (a dev server's .next is untouched; API_URL is read at runtime)
#                 start: serve an existing build (apps/web/$E2E_DIST, default .next)
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
API_PORT="${E2E_API_PORT:-18765}"
WEB_PORT="${E2E_WEB_PORT:-13765}"
MODE="${E2E_WEB_MODE:-dev}"
RUN="$HERE/.servers"

for port in "$API_PORT" "$WEB_PORT"; do
  if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
    echo "[e2e] port $port is already in use; set E2E_API_PORT / E2E_WEB_PORT" >&2
    exit 1
  fi
done

rm -rf "$RUN/data" "$RUN/meta.db"
# Shared secret between the web proxy and the API (see docs/security.md, "Deployment model").
export AOS_PROXY_SECRET="${AOS_PROXY_SECRET:-$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')}"
mkdir -p "$RUN/data/demo"
# Reuse the repository's verified demo cache when present (the API re-verifies its hashes).
if [ -d "$ROOT/data/demo/summit-supply-seed42" ]; then
  ln -s "$ROOT/data/demo/summit-supply-seed42" "$RUN/data/demo/summit-supply-seed42"
fi

pids=()
cleanup() {
  for pid in "${pids[@]:-}"; do
    [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "[e2e] API on :$API_PORT (logs: $RUN/api.log)"
(
  cd "$ROOT"
  DATABASE_URL="sqlite:///$RUN/meta.db" \
  DATA_DIR="$RUN/data" \
  AOS_ENV=development \
  AUTH_MODE=local \
  AOS_HOST=127.0.0.1 \
  AOS_PORT="$API_PORT" \
  AOS_LOG_JSON=false \
  AOS_CORS_ORIGINS="[\"http://127.0.0.1:$WEB_PORT\",\"http://localhost:$WEB_PORT\"]" \
  exec uv run --no-sync analystos-api
) >"$RUN/api.log" 2>&1 &
pids+=($!)

for _ in $(seq 1 120); do
  if curl -fsS "http://127.0.0.1:$API_PORT/api/health" >/dev/null 2>&1; then break; fi
  if ! kill -0 "${pids[0]}" 2>/dev/null; then echo "[e2e] API exited"; cat "$RUN/api.log"; exit 1; fi
  sleep 0.5
done
curl -fsS "http://127.0.0.1:$API_PORT/api/health" >/dev/null || { echo "[e2e] API not healthy"; cat "$RUN/api.log"; exit 1; }

echo "[e2e] web ($MODE) on :$WEB_PORT (logs: $RUN/web.log)"
WEB_DIR="$ROOT/apps/web"
DIST="${E2E_DIST:-.next}"
if [ "$MODE" = "build" ]; then
  # Build in apps/web itself into a separate dist dir (.next-e2e, git-ignored) so a running dev
  # server's .next is untouched. Building a copy under tests/e2e made Next pick the e2e lockfile as
  # the workspace root and stall. `next build` rewrites next-env.d.ts/tsconfig.json for a custom
  # dist dir; both are restored afterwards.
  DIST=".next-e2e"
  echo "[e2e] building web into apps/web/$DIST (logs: $RUN/web-build.log)"
  cp "$WEB_DIR/next-env.d.ts" "$RUN/next-env.d.ts.bak"
  cp "$WEB_DIR/tsconfig.json" "$RUN/tsconfig.json.bak"
  build_ok=0
  (cd "$WEB_DIR" && NEXT_DIST_DIR="$DIST" NEXT_TELEMETRY_DISABLED=1 \
    NODE_OPTIONS="${NODE_OPTIONS:---max-old-space-size=3072}" pnpm exec next build) >"$RUN/web-build.log" 2>&1 || build_ok=$?
  cp "$RUN/next-env.d.ts.bak" "$WEB_DIR/next-env.d.ts"
  cp "$RUN/tsconfig.json.bak" "$WEB_DIR/tsconfig.json"
  if [ "$build_ok" != 0 ]; then echo "[e2e] web build failed"; tail -50 "$RUN/web-build.log"; exit 1; fi
fi

(
  cd "$WEB_DIR"
  export API_URL="http://127.0.0.1:$API_PORT" NEXT_TELEMETRY_DISABLED=1 NEXT_DIST_DIR="$DIST"
  # server.mjs records each request's socket address for the /api proxy (see apps/web/server.mjs).
  if [ "$MODE" = "start" ] || [ "$MODE" = "build" ]; then
    exec node server.mjs -p "$WEB_PORT" -H 127.0.0.1
  else
    exec node server.mjs --dev -p "$WEB_PORT" -H 127.0.0.1
  fi
) >"$RUN/web.log" 2>&1 &
pids+=($!)

if [ "$MODE" = "dev" ]; then
  # Compile every route the flow visits before the browser gets there (dev compiles on demand).
  for _ in $(seq 1 120); do curl -fsS -o /dev/null "http://127.0.0.1:$WEB_PORT/" 2>/dev/null && break; sleep 1; done
  for route in / /w/warmup /w/warmup/data /w/warmup/data/warmup /w/warmup/data/relationships \
               /w/warmup/metrics /w/warmup/investigate /w/warmup/investigate/warmup /w/warmup/findings \
               /w/warmup/reports /w/warmup/reports/warmup; do
    curl -s -o /dev/null --max-time 600 "http://127.0.0.1:$WEB_PORT$route" || true
  done
  echo "[e2e] web routes compiled"
else
  for _ in $(seq 1 600); do curl -fsS -o /dev/null "http://127.0.0.1:$WEB_PORT/" 2>/dev/null && break; sleep 1; done
fi
echo "[e2e] web ready"

wait -n "${pids[@]}"
echo "[e2e] a server exited; see $RUN/*.log"
exit 1
