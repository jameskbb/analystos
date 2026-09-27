import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": fileURLToPath(new URL("./", import.meta.url)) } },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./test/setup.ts"],
    include: ["**/*.test.{ts,tsx}"],
    exclude: ["node_modules", ".next"],
    css: false,
    // jsdom workers are memory-hungry: the default (one per core) exhausted RAM on shared
    // 24-core hosts and never finished. Two workers run the suite in ~10 s; override with
    // VITEST_MAX_WORKERS when a machine has more headroom.
    pool: "forks",
    maxWorkers: Number(process.env.VITEST_MAX_WORKERS ?? 2),
    testTimeout: 20_000,
  },
});
