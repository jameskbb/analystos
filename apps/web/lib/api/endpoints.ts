/**
 * Endpoint functions grouped by API router. Every workspace-scoped call takes the workspace id first.
 * Each area lives in its own module under ./resources so screens can evolve independently.
 */
export * from "./resources/core";
export * from "./resources/platform";
export * from "./resources/data";
export * from "./resources/semantic";
export * from "./resources/workbench";
export * from "./resources/investigations";
export * from "./resources/outputs";
export * from "./resources/analysis";
