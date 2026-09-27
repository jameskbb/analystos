/** Number, date and change formatting. All numeric UI goes through here so output stays consistent. */

export type NumberFormat = "currency" | "number" | "percent" | "integer" | string | null | undefined;

const nf = (opts: Intl.NumberFormatOptions) => new Intl.NumberFormat("en-US", opts);
const cache = new Map<string, Intl.NumberFormat>();
function fmt(key: string, opts: Intl.NumberFormatOptions): Intl.NumberFormat {
  let f = cache.get(key);
  if (!f) {
    f = nf(opts);
    cache.set(key, f);
  }
  return f;
}

export function toNumber(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "bigint") return Number(v);
  if (typeof v === "string") {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

/** Full-precision business formatting ($1,234,567 / 12.3% / 1,234). */
export function formatValue(v: unknown, format?: NumberFormat, opts: { compact?: boolean; digits?: number } = {}): string {
  const n = toNumber(v);
  if (n === null) return v === null || v === undefined ? "n/a" : String(v);
  if (opts.compact) return formatCompact(n, format);
  switch (format) {
    case "currency":
      return fmt(`cur${opts.digits ?? "a"}`, {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: opts.digits ?? (Math.abs(n) >= 1000 ? 0 : 2),
        minimumFractionDigits: opts.digits ?? (Math.abs(n) >= 1000 ? 0 : 2),
      }).format(n);
    case "percent":
      return fmt(`pct${opts.digits ?? 1}`, {
        style: "percent",
        maximumFractionDigits: opts.digits ?? 1,
        minimumFractionDigits: opts.digits ?? 1,
      }).format(n);
    case "integer":
      return fmt("int", { maximumFractionDigits: 0 }).format(n);
    default: {
      const digits = opts.digits ?? (Number.isInteger(n) ? 0 : Math.abs(n) >= 100 ? 1 : Math.abs(n) >= 1 ? 2 : 4);
      return fmt(`num${digits}`, { maximumFractionDigits: digits }).format(n);
    }
  }
}

/** Compact formatting for axes, KPIs and tree nodes ($4.2M, 12.3K, 11.8%). */
export function formatCompact(v: unknown, format?: NumberFormat): string {
  const n = toNumber(v);
  if (n === null) return "n/a";
  if (format === "percent") return formatValue(n, "percent");
  const abs = Math.abs(n);
  const body =
    abs >= 1e9
      ? `${trim(n / 1e9)}B`
      : abs >= 1e6
        ? `${trim(n / 1e6)}M`
        : abs >= 1e4
          ? `${trim(n / 1e3)}K`
          : abs >= 100 || Number.isInteger(n)
            ? fmt("c0", { maximumFractionDigits: 0 }).format(n)
            : fmt("c2", { maximumFractionDigits: 2 }).format(n);
  if (format === "currency") return n < 0 ? `-$${body.replace("-", "")}` : `$${body}`;
  return body;
}

function trim(n: number): string {
  const abs = Math.abs(n);
  const fixed = abs >= 100 ? n.toFixed(0) : abs >= 10 ? n.toFixed(1) : n.toFixed(2);
  return fixed.includes(".") ? fixed.replace(/\.?0+$/, "") : fixed;
}

/** Signed percentage change (+2.8% / −11.8%). Input is a ratio (−0.118). */
export function formatPctChange(ratio: unknown, digits = 1): string {
  const n = toNumber(ratio);
  if (n === null) return "n/a";
  const s = `${Math.abs(n * 100).toFixed(digits)}%`;
  if (n > 0) return `+${s}`;
  if (n < 0) return `−${s}`;
  return s;
}

/**
 * Signed absolute change using the metric's format (−$420K). For percentage metrics the absolute
 * change is in percentage points (26.3% → 21.6% is −4.8 pp, not −4.8%).
 */
export function formatSignedChange(v: unknown, format?: NumberFormat): string {
  const n = toNumber(v);
  if (n === null) return "n/a";
  const body = format === "percent" ? `${(Math.abs(n) * 100).toFixed(1)} pp` : formatCompact(Math.abs(n), format);
  if (n > 0) return `+${body}`;
  if (n < 0) return `−${body}`;
  return body;
}

export function formatShare(v: unknown): string {
  const n = toNumber(v);
  if (n === null) return "n/a";
  return `${(n * 100).toFixed(Math.abs(n) < 0.1 ? 1 : 0)}%`;
}

export function changeTone(v: unknown, higherIsBetter = true): "positive" | "negative" | "neutral" {
  const n = toNumber(v);
  if (n === null || n === 0) return "neutral";
  return (n > 0) === higherIsBetter ? "positive" : "negative";
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "n/a";
  const units = ["B", "KB", "MB", "GB"];
  let v = bytes;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "n/a";
  if (ms < 1) return "<1 ms";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;
}

export function formatInt(n: number | null | undefined): string {
  if (n === null || n === undefined) return "n/a";
  return fmt("int", { maximumFractionDigits: 0 }).format(n);
}

function parseDate(d: string | Date | null | undefined): Date | null {
  if (!d) return null;
  const date = typeof d === "string" ? new Date(/[zZ]|[+-]\d\d:\d\d$/.test(d) || d.length <= 10 ? d : `${d}Z`) : d;
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDate(d: string | Date | null | undefined): string {
  const date = parseDate(d);
  if (!date) return "n/a";
  return date.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
}

export function formatDateTime(d: string | Date | null | undefined): string {
  const date = parseDate(d);
  if (!date) return "n/a";
  return date.toLocaleString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function formatRelative(d: string | Date | null | undefined, now: Date = new Date()): string {
  const date = parseDate(d);
  if (!date) return "n/a";
  const diff = (now.getTime() - date.getTime()) / 1000;
  if (diff < 45) return "just now";
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  if (diff < 86_400) return `${Math.round(diff / 3600)}h ago`;
  if (diff < 86_400 * 7) return `${Math.round(diff / 86_400)}d ago`;
  return formatDate(date);
}

/** Human label for a date window: "Aug 1 – 31, 2026". */
export function formatWindow(start?: string | null, end?: string | null): string {
  const s = parseDate(start ?? null);
  const e = parseDate(end ?? null);
  if (!s || !e) return start || end || "n/a";
  const opts: Intl.DateTimeFormatOptions = { month: "short", day: "numeric", timeZone: "UTC" };
  const sameYear = s.getUTCFullYear() === e.getUTCFullYear();
  const sameMonth = sameYear && s.getUTCMonth() === e.getUTCMonth();
  if (sameMonth)
    return `${s.toLocaleDateString("en-US", opts)} – ${e.getUTCDate()}, ${e.getUTCFullYear()}`;
  if (sameYear)
    return `${s.toLocaleDateString("en-US", opts)} – ${e.toLocaleDateString("en-US", opts)}, ${e.getUTCFullYear()}`;
  return `${s.toLocaleDateString("en-US", { ...opts, year: "numeric" })} – ${e.toLocaleDateString("en-US", { ...opts, year: "numeric" })}`;
}

export function humanize(s: string | null | undefined): string {
  if (!s) return "";
  return s.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function pluralize(n: number, one: string, many = `${one}s`): string {
  return `${formatInt(n)} ${n === 1 ? one : many}`;
}

/** Value rendering for grid cells. */
export function formatCell(v: unknown, type?: string): string {
  if (v === null || v === undefined) return "NULL";
  if (typeof v === "number") {
    if (type && /int/i.test(type)) return fmt("int", { maximumFractionDigits: 0 }).format(v);
    return Number.isInteger(v) ? fmt("int", { maximumFractionDigits: 0 }).format(v) : fmt("cell", { maximumFractionDigits: 4 }).format(v);
  }
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export function isNumericType(type: string | undefined | null): boolean {
  if (!type) return false;
  return /^(tinyint|smallint|integer|int|bigint|hugeint|utinyint|usmallint|uinteger|ubigint|float|double|real|decimal|numeric|number)/i.test(
    type.trim(),
  ) || /int|float|double|decimal|numeric/i.test(type);
}

export function isTemporalType(type: string | undefined | null): boolean {
  if (!type) return false;
  return /date|time/i.test(type);
}
