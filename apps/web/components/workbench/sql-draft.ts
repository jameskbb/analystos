/** The SQL editor's unsaved draft lives in localStorage per workspace; other screens can hand SQL to it. */
export const sqlDraftKey = (ws: string) => `aos-sql-draft:${ws}`;

export function writeSqlDraft(ws: string, sql: string): void {
  try {
    localStorage.setItem(sqlDraftKey(ws), sql);
  } catch {
    /* storage unavailable */
  }
}
