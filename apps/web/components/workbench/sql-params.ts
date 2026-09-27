/**
 * SQL parameter detection. The API binds `$name` placeholders; the editor also accepts the
 * `{{name}}` template form and rewrites it to `$name` before running. Placeholders inside
 * string literals, quoted identifiers and comments are ignored. `:name` is not supported because
 * it collides with DuckDB's `::type` casts.
 */
import type { ParamType, QueryParameterDef } from "@/lib/api/resources/workbench";

type Segment = { code: boolean; text: string };

/** Splits SQL into code and non-code (strings, quoted identifiers, comments) segments. */
export function splitSqlSegments(sql: string): Segment[] {
  const out: Segment[] = [];
  let buf = "";
  let i = 0;
  const flush = (code: boolean) => {
    if (buf) out.push({ code, text: buf });
    buf = "";
  };
  while (i < sql.length) {
    const c = sql[i];
    const next = sql[i + 1];
    if (c === "-" && next === "-") {
      flush(true);
      const end = sql.indexOf("\n", i);
      const stop = end === -1 ? sql.length : end;
      out.push({ code: false, text: sql.slice(i, stop) });
      i = stop;
      continue;
    }
    if (c === "/" && next === "*") {
      flush(true);
      const end = sql.indexOf("*/", i + 2);
      const stop = end === -1 ? sql.length : end + 2;
      out.push({ code: false, text: sql.slice(i, stop) });
      i = stop;
      continue;
    }
    if (c === "'" || c === '"') {
      flush(true);
      let j = i + 1;
      while (j < sql.length) {
        if (sql[j] === c) {
          if (sql[j + 1] === c) {
            j += 2;
            continue;
          }
          break;
        }
        j++;
      }
      const stop = Math.min(j + 1, sql.length);
      out.push({ code: false, text: sql.slice(i, stop) });
      i = stop;
      continue;
    }
    buf += c;
    i++;
  }
  flush(true);
  return out;
}

const DOLLAR = /\$([A-Za-z_][A-Za-z0-9_]*)/g;
const MUSTACHE = /\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g;

/** Parameter names in order of first appearance. */
export function detectParams(sql: string): string[] {
  const names: string[] = [];
  for (const seg of splitSqlSegments(sql)) {
    if (!seg.code) continue;
    const found: { idx: number; name: string }[] = [];
    for (const m of seg.text.matchAll(DOLLAR)) found.push({ idx: m.index ?? 0, name: m[1] });
    for (const m of seg.text.matchAll(MUSTACHE)) found.push({ idx: m.index ?? 0, name: m[1] });
    found.sort((a, b) => a.idx - b.idx).forEach((f) => {
      if (!names.includes(f.name)) names.push(f.name);
    });
  }
  return names;
}

/** Rewrites `{{name}}` to `$name` outside strings and comments. */
export function normalizeParams(sql: string): string {
  return splitSqlSegments(sql)
    .map((s) => (s.code ? s.text.replace(MUSTACHE, (_m, n: string) => `$${n}`) : s.text))
    .join("");
}

/** A reasonable default type from the parameter name. */
export function guessParamType(name: string): ParamType {
  const n = name.toLowerCase();
  if (/(date|day|start|end|from|to|since|until|month)$/.test(n) || /^(start|end)_/.test(n)) return "date";
  if (/(limit|count|n|top|days|year|qty|quantity)$/.test(n)) return "integer";
  if (/(amount|threshold|min|max|pct|rate|price)$/.test(n)) return "number";
  if (/(regions|ids|list|values|names)$/.test(n)) return "string_list";
  if (/^(is|has|include)_/.test(n)) return "boolean";
  return "string";
}

/** Keeps definitions for params still present, adds new ones with guessed types, preserves order of appearance. */
export function syncParamDefs(names: string[], defs: QueryParameterDef[]): QueryParameterDef[] {
  return names.map((name) => defs.find((d) => d.name === name) ?? { name, type: guessParamType(name), required: true });
}

/** Converts a text input value to the typed value the API binds. */
export function coerceParamValue(raw: string, type: ParamType): unknown {
  const v = raw.trim();
  if (v === "") return null;
  switch (type) {
    case "integer": {
      const n = Number(v);
      return Number.isInteger(n) ? n : v;
    }
    case "number": {
      const n = Number(v);
      return Number.isFinite(n) ? n : v;
    }
    case "boolean":
      return v === "true" || v === "1" || v.toLowerCase() === "yes";
    case "string_list":
      return v
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
    default:
      return v;
  }
}

export function paramValueToText(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (Array.isArray(v)) return v.join(", ");
  return String(v);
}

/** Validates required values; returns a message per missing/invalid param. */
export function validateParamValues(defs: QueryParameterDef[], values: Record<string, string>): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const d of defs) {
    const raw = values[d.name] ?? paramValueToText(d.default);
    if (!raw.trim()) {
      if (d.required !== false) errors[d.name] = "Required";
      continue;
    }
    if ((d.type === "integer" || d.type === "number") && !Number.isFinite(Number(raw)))
      errors[d.name] = "Must be a number";
    else if (d.type === "integer" && !Number.isInteger(Number(raw))) errors[d.name] = "Must be a whole number";
    else if (d.type === "date" && !/^\d{4}-\d{2}-\d{2}/.test(raw.trim())) errors[d.name] = "Use YYYY-MM-DD";
  }
  return errors;
}
