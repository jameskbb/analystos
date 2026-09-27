/** Mirrors the engine's identifier normalization so the suggested DuckDB table name matches what ingest uses. */
export function suggestTableName(...parts: (string | null | undefined)[]): string {
  const text = parts
    .filter(Boolean)
    .map((p) => String(p).replace(/\.(csv|tsv|txt|xlsx|xlsm|xls|parquet|json|ndjson|jsonl)$/i, ""))
    .join("_")
    .toLowerCase()
    .replace(/[^0-9a-z]+/g, "_")
    .replace(/_+/g, "_")
    .replace(/^_|_$/g, "");
  if (!text) return "dataset";
  return (/^\d/.test(text) ? `t_${text}` : text).slice(0, 63);
}

export const TABLE_NAME_RE = /^[a-z_][a-z0-9_]{0,62}$/;

export function datasetTitle(name: string): string {
  return name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
