/**
 * Pure logic for dashboards and reports: date presets, grid layout operations,
 * report review gating and the executive-summary builder. No React, fully unit-tested.
 */
import type { Finding, LayoutItem, TileKind } from "@/lib/api/types";
import type {
  DashboardDateRange,
  FindingSnapshot,
  ReportBlockSpec,
  ReportOut,
  SourceItem,
  SummaryEntry,
} from "@/lib/api/resources/outputs";

/* ------------------------------------------------------------------ dates */

export type DatePreset = "all" | "last_month" | "this_month" | "qtd" | "ytd" | "rolling_30" | "rolling_90" | "custom";

export const DATE_PRESETS: { value: DatePreset; label: string; text: string | null }[] = [
  { value: "all", label: "All time", text: null },
  { value: "last_month", label: "Last month", text: "last month" },
  { value: "this_month", label: "Month to date", text: "this month" },
  { value: "qtd", label: "Quarter to date", text: "QTD" },
  { value: "ytd", label: "Year to date", text: "YTD" },
  { value: "rolling_30", label: "Rolling 30 days", text: "rolling 30 days" },
  { value: "rolling_90", label: "Rolling 90 days", text: "rolling 90 days" },
  { value: "custom", label: "Custom range", text: null },
];

const iso = (d: Date) => d.toISOString().slice(0, 10);
const utc = (y: number, m: number, d: number) => new Date(Date.UTC(y, m, d));

/**
 * Calendar-month interpretation of a preset relative to `today` (UTC, inclusive ends).
 * Quarter/year starts honour the fiscal year start month. The server resolves the same phrases
 * with the workspace calendar; this is used for labels and the custom-range default.
 */
export function presetRange(
  preset: DatePreset,
  today: Date,
  fiscalYearStartMonth = 1,
): { start: string; end: string } | null {
  const y = today.getUTCFullYear();
  const m = today.getUTCMonth();
  const d = today.getUTCDate();
  switch (preset) {
    case "last_month":
      return { start: iso(utc(y, m - 1, 1)), end: iso(utc(y, m, 0)) };
    case "this_month":
      return { start: iso(utc(y, m, 1)), end: iso(utc(y, m, d)) };
    case "qtd": {
      const fm = fiscalYearStartMonth - 1;
      const offset = (((m - fm) % 12) + 12) % 12;
      const qStartMonth = m - (offset % 3);
      return { start: iso(utc(y, qStartMonth, 1)), end: iso(utc(y, m, d)) };
    }
    case "ytd": {
      const fm = fiscalYearStartMonth - 1;
      const startYear = m >= fm ? y : y - 1;
      return { start: iso(utc(startYear, fm, 1)), end: iso(utc(y, m, d)) };
    }
    case "rolling_30":
      return { start: iso(utc(y, m, d - 29)), end: iso(utc(y, m, d)) };
    case "rolling_90":
      return { start: iso(utc(y, m, d - 89)), end: iso(utc(y, m, d)) };
    default:
      return null;
  }
}

/** Request body for a preset: phrases go to the server calendar; custom ranges are explicit. */
export function dateRangeFor(
  preset: DatePreset,
  custom?: { start: string; end: string } | null,
): DashboardDateRange | null {
  if (preset === "all") return null;
  if (preset === "custom") return custom?.start && custom?.end ? { start: custom.start, end: custom.end } : null;
  const text = DATE_PRESETS.find((p) => p.value === preset)?.text ?? null;
  return text ? { text } : null;
}

/** Inverse of dateRangeFor, for hydrating the control from a saved dashboard. */
export function presetFromRange(range: DashboardDateRange | null | undefined): {
  preset: DatePreset;
  custom: { start: string; end: string } | null;
} {
  if (!range) return { preset: "all", custom: null };
  if (range.text) {
    const hit = DATE_PRESETS.find((p) => p.text && p.text.toLowerCase() === range.text!.toLowerCase());
    if (hit) return { preset: hit.value, custom: null };
  }
  if (range.start && range.end) return { preset: "custom", custom: { start: range.start, end: range.end } };
  return { preset: "all", custom: null };
}

/** The latest complete calendar month before `today`, as YYYY-MM. */
export function latestFullMonth(today: Date): string {
  const d = utc(today.getUTCFullYear(), today.getUTCMonth() - 1, 1);
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

export function monthLabel(period: string | null | undefined): string {
  if (!period || !/^\d{4}-\d{2}$/.test(period)) return period ?? "";
  const [y, m] = period.split("-").map(Number);
  return utc(y, m - 1, 1).toLocaleDateString("en-US", { month: "long", year: "numeric", timeZone: "UTC" });
}

/* ------------------------------------------------------------------ grid layout */

export const GRID_COLS = 12;

export const DEFAULT_TILE_SIZE: Record<TileKind, { w: number; h: number }> = {
  kpi: { w: 3, h: 3 },
  chart: { w: 6, h: 7 },
  table: { w: 6, h: 7 },
  text: { w: 4, h: 3 },
};

export const MIN_TILE_SIZE: Record<TileKind, { w: number; h: number }> = {
  kpi: { w: 2, h: 2 },
  chart: { w: 3, h: 4 },
  table: { w: 3, h: 4 },
  text: { w: 2, h: 2 },
};

/** Places a new tile in the first free slot scanning rows top-to-bottom. */
export function placeTile(layout: LayoutItem[], id: string, size: { w: number; h: number }): LayoutItem {
  const w = Math.min(size.w, GRID_COLS);
  const occupied = (x: number, y: number) =>
    layout.some((it) => x < it.x + it.w && x + w > it.x && y < it.y + it.h && y + size.h > it.y);
  const maxY = layout.reduce((m, it) => Math.max(m, it.y + it.h), 0);
  for (let y = 0; y <= maxY; y++) {
    for (let x = 0; x + w <= GRID_COLS; x++) {
      if (!occupied(x, y)) return { i: id, x, y, w, h: size.h };
    }
  }
  return { i: id, x: 0, y: maxY, w, h: size.h };
}

export type LayoutMove = "left" | "right" | "up" | "down" | "wider" | "narrower" | "taller" | "shorter";

/** Keyboard/menu fallback for drag and resize. The grid compacts vertically afterwards. */
export function nudgeLayout(layout: LayoutItem[], id: string, move: LayoutMove, min = { w: 1, h: 1 }): LayoutItem[] {
  return layout.map((it) => {
    if (it.i !== id) return it;
    const n = { ...it };
    switch (move) {
      case "left":
        n.x = Math.max(0, it.x - 1);
        break;
      case "right":
        n.x = Math.min(GRID_COLS - it.w, it.x + 1);
        break;
      case "up":
        n.y = Math.max(0, it.y - 1);
        break;
      case "down":
        n.y = it.y + 1;
        break;
      case "wider":
        n.w = Math.min(GRID_COLS - it.x, it.w + 1);
        break;
      case "narrower":
        n.w = Math.max(min.w, it.w - 1);
        break;
      case "taller":
        n.h = it.h + 1;
        break;
      case "shorter":
        n.h = Math.max(min.h, it.h - 1);
        break;
    }
    return n;
  });
}

export function sameLayout(a: LayoutItem[], b: LayoutItem[]): boolean {
  if (a.length !== b.length) return false;
  const key = (l: LayoutItem[]) =>
    [...l]
      .sort((x, y) => x.i.localeCompare(y.i))
      .map((i) => `${i.i}:${i.x},${i.y},${i.w},${i.h}`)
      .join("|");
  return key(a) === key(b);
}

/** Layout entries for every tile, adding any tile the stored layout does not know about. */
export function reconcileLayout(layout: LayoutItem[], tiles: { id: string; kind: TileKind }[]): LayoutItem[] {
  const ids = new Set(tiles.map((t) => t.id));
  const out = layout.filter((l) => ids.has(l.i)).map((l) => ({ i: l.i, x: l.x, y: l.y, w: l.w, h: l.h }));
  for (const t of tiles) {
    if (!out.some((l) => l.i === t.id)) out.push(placeTile(out, t.id, DEFAULT_TILE_SIZE[t.kind] ?? { w: 4, h: 4 }));
  }
  return out;
}

/* ------------------------------------------------------------------ report blocks */

export const BLOCK_LABELS: Record<string, string> = {
  heading: "Heading",
  narrative: "Narrative",
  finding: "Finding",
  kpi: "KPI",
  chart: "Chart",
  table: "Table",
  methodology: "Methodology",
  sources: "Sources",
  summary: "Executive summary",
  open_questions: "Open questions",
};

export function newBlockId(): string {
  return `b_${Math.random().toString(36).slice(2, 10)}${Date.now().toString(36).slice(-4)}`;
}

export function newBlock(type: string): ReportBlockSpec {
  const base: ReportBlockSpec = { id: newBlockId(), type };
  switch (type) {
    case "heading":
      return { ...base, text: "" };
    case "narrative":
    case "methodology":
      return { ...base, markdown: "" };
    case "sources":
    case "open_questions":
      return { ...base, items: [] };
    case "summary":
      return { ...base, observations: [], supported_explanations: [], hypotheses: [] };
    default:
      return base;
  }
}

export function moveBlock(blocks: ReportBlockSpec[], id: string, delta: -1 | 1): ReportBlockSpec[] {
  const i = blocks.findIndex((b) => b.id === id);
  const j = i + delta;
  if (i < 0 || j < 0 || j >= blocks.length) return blocks;
  const next = [...blocks];
  [next[i], next[j]] = [next[j], next[i]];
  return next;
}

/* ------------------------------------------------------------------ review gating */

export interface ReviewState {
  total: number;
  included: number;
  reviewed: number;
  pending: ReportBlockSpec[];
  canSubmit: boolean;
  canPublish: boolean;
  /** Why publishing is blocked, for the disabled button's explanation. */
  blockers: string[];
  readOnly: boolean;
}

/**
 * Review-before-publish (spec §64): every included block must be marked reviewed before a report
 * can be published; excluded blocks never publish. Published reports are read-only.
 */
export function reviewState(report: Pick<ReportOut, "status" | "blocks">): ReviewState {
  const blocks = report.blocks ?? [];
  const included = blocks.filter((b) => !b.excluded);
  const reviewed = included.filter((b) => b.reviewed);
  const pending = included.filter((b) => !b.reviewed);
  const readOnly = report.status === "published";
  const blockers: string[] = [];
  if (!included.length) blockers.push("The report has no included blocks.");
  if (pending.length) blockers.push(`${pending.length} included block${pending.length === 1 ? "" : "s"} not yet reviewed.`);
  if (report.status === "draft") blockers.push("Submit the report for review first.");
  if (readOnly) blockers.push("The report is already published.");
  return {
    total: blocks.length,
    included: included.length,
    reviewed: reviewed.length,
    pending,
    canSubmit: report.status === "draft" && included.length > 0,
    canPublish: report.status === "in_review" && included.length > 0 && pending.length === 0,
    blockers,
    readOnly,
  };
}

export type PublishRefusal =
  | { kind: "unreviewed"; blockIds: string[]; message: string }
  | { kind: "not_in_review"; message: string }
  | { kind: "other"; message: string };

/**
 * Reads the API's publish refusal (R-15): `409 unreviewed_blocks` lists `errors.block_ids`;
 * `409 not_in_review` means the report was never submitted. The server is authoritative even when
 * the local checklist looks complete (e.g. another editor changed a block).
 */
export function publishRefusal(err: unknown): PublishRefusal {
  const e = err as { code?: string | null; errors?: unknown; message?: string } | null;
  const message = e?.message ?? String(err);
  if (e?.code === "unreviewed_blocks") {
    const raw = e.errors && typeof e.errors === "object" ? (e.errors as { block_ids?: unknown }).block_ids : null;
    const ids = Array.isArray(raw) ? raw.map(String) : [];
    return { kind: "unreviewed", blockIds: ids, message };
  }
  if (e?.code === "not_in_review") return { kind: "not_in_review", message };
  return { kind: "other", message };
}

/** Editing content invalidates its review: a changed block must be looked at again. */
export function editBlock(block: ReportBlockSpec, patch: Partial<ReportBlockSpec>): ReportBlockSpec {
  const contentKeys = Object.keys(patch).filter((k) => k !== "reviewed" && k !== "excluded" && k !== "review_note");
  return { ...block, ...patch, ...(contentKeys.length && block.reviewed ? { reviewed: false } : {}) };
}

/* ------------------------------------------------------------------ findings & summary */

export function snapshotFinding(f: Finding): FindingSnapshot {
  return {
    statement: f.statement,
    statement_type: f.statement_type,
    evidence_strength: f.evidence_strength,
    evidence_reasons: f.evidence_reasons ?? [],
    status: f.status,
    filter_context: f.filter_context ?? [],
    values: f.values,
    metric_version_ids: f.metric_version_ids,
    investigation_id: f.investigation_id ?? null,
  };
}

export function entryText(e: SummaryEntry): string {
  return typeof e === "string" ? e : e.text;
}

/**
 * Executive summary from confirmed findings only (spec §38, §65): observed facts, supported
 * explanations and hypotheses are kept in separate sections and never mixed.
 */
export function buildSummaryBlock(findings: Finding[], id = newBlockId()): ReportBlockSpec {
  const confirmed = findings.filter((f) => f.status === "confirmed");
  const pick = (t: string) =>
    confirmed
      .filter((f) => (f.statement_type === t) || (t === "observation" && !["supported_explanation", "hypothesis"].includes(f.statement_type)))
      .map((f) => ({ text: f.statement, finding_id: f.id, evidence_strength: f.evidence_strength }));
  return {
    id,
    type: "summary",
    title: "Executive summary",
    observations: pick("observation"),
    supported_explanations: pick("supported_explanation"),
    hypotheses: pick("hypothesis"),
  };
}

/** Collects source references (metrics with versions, investigations, datasets) from blocks. */
export function collectSources(blocks: ReportBlockSpec[]): SourceItem[] {
  const seen = new Map<string, SourceItem>();
  const add = (s: SourceItem) => {
    const key = `${s.kind}:${s.ref_id ?? s.label}:${s.version ?? ""}`;
    if (!seen.has(key)) seen.set(key, s);
  };
  for (const b of blocks) {
    if (b.excluded) continue;
    const snap = b.snapshot;
    if (snap?.investigation_id) add({ kind: "investigation", label: "Investigation", ref_id: snap.investigation_id });
    const mv = snap?.metric_version_ids ?? {};
    for (const [metric, v] of Object.entries(mv)) {
      const version =
        typeof v === "object" && v !== null ? ((v as { version_no?: number }).version_no ?? null) : (v as number | string | null);
      add({ kind: "metric", label: metric, ref_id: metric, version });
    }
    if (b.type === "finding" && b.finding_id) add({ kind: "finding", label: snap?.statement ?? "Finding", ref_id: b.finding_id });
    if (b.metric_id) add({ kind: "metric", label: b.label ?? b.metric_id, ref_id: b.metric_id });
    const p = b.provenance;
    for (const d of p?.datasets ?? (p?.dataset ? [p.dataset] : [])) add({ kind: "dataset", label: d, ref_id: null });
    if (p?.metric) add({ kind: "metric", label: p.metric, ref_id: p.metric, version: p.metric_version ?? null });
    if (b.artifact_id) add({ kind: "artifact", label: b.title ?? "Artifact", ref_id: b.artifact_id });
  }
  return [...seen.values()];
}
