/** API resource functions (investigations, findings). Paths follow apps/api (see API.md / OpenAPI). */
import { api } from "../client";
import type * as T from "../types";
import { w, e, list, type List } from "./core";

/* ------------------------------------------------------------------ investigations */

export const investigations = {
  list: (ws: string, params: { status?: string; limit?: number } = {}) =>
    list(api.get<List<T.Investigation>>(`${w(ws)}/investigations`, params)),
  get: (ws: string, id: string) => api.get<T.Investigation>(`${w(ws)}/investigations/${e(id)}`),
  templates: (ws: string) =>
    list(api.get<List<{ id: string; name: string; description: string; question?: string; metric_roles?: string[] }>>(
      `${w(ws)}/investigations/templates`,
    )),
  /** Creates and interprets the question; plans automatically. Runs immediately when no approval is required. */
  create: (ws: string, body: { question: string; template?: string | null; auto_run?: boolean; choices?: Record<string, string> }) =>
    api.post<T.Investigation>(`${w(ws)}/investigations`, body),
  /** Resolves ambiguous metric terms chosen by the analyst, then re-interprets and re-plans. */
  disambiguate: (ws: string, id: string, choices: Record<string, string>) =>
    api.post<T.Investigation>(`${w(ws)}/investigations/${e(id)}/disambiguate`, { choices }),
  interpret: (ws: string, question: string) =>
    api.post<T.Interpretation>(`${w(ws)}/investigations/interpret`, { question }),
  update: (ws: string, id: string, body: { title?: string }) =>
    api.patch<T.Investigation>(`${w(ws)}/investigations/${e(id)}`, body),
  remove: (ws: string, id: string) => api.del(`${w(ws)}/investigations/${e(id)}`),
  replan: (ws: string, id: string) => api.post<T.Investigation>(`${w(ws)}/investigations/${e(id)}/plan`),
  updatePlan: (ws: string, id: string, plan: T.AnalysisPlan) =>
    api.put<T.Investigation>(`${w(ws)}/investigations/${e(id)}/plan`, plan),
  summary: (ws: string, id: string) => api.get<T.ExecutiveSummary>(`${w(ws)}/investigations/${e(id)}/summary`),
  run: (ws: string, id: string) => api.post<T.Investigation | T.JobAccepted>(`${w(ws)}/investigations/${e(id)}/run`),
  rerun: (ws: string, id: string) =>
    api.post<T.Investigation | T.JobAccepted>(`${w(ws)}/investigations/${e(id)}/rerun`),
  runs: (ws: string, id: string) => list(api.get<List<T.InvestigationRun>>(`${w(ws)}/investigations/${e(id)}/runs`)),
  diff: (ws: string, id: string, params: { from_run?: string; to_run?: string } = {}) =>
    api
      .get<T.InvestigationDiff | { diff: T.InvestigationDiff; from_run_no?: number; to_run_no?: number }>(
        `${w(ws)}/investigations/${e(id)}/diff`,
        params,
      )
      .then((r): T.InvestigationDiff =>
        "diff" in r && r.diff
          ? { ...r.diff, from_run: r.from_run_no ?? r.diff.from_run ?? null, to_run: r.to_run_no ?? r.diff.to_run ?? null }
          : (r as T.InvestigationDiff),
      ),
  artifacts: (ws: string, id: string) =>
    list(api.get<List<T.ArtifactRecord>>(`${w(ws)}/investigations/${e(id)}/artifacts`)),
  drill: (ws: string, id: string, nodeId: string, dimension: string) =>
    api.post<T.Investigation>(`${w(ws)}/investigations/${e(id)}/nodes/${e(nodeId)}/drill`, { dimension }),
  /** Node review actions. `note` carries the rejection reason or the annotation text. */
  nodeAction: (
    ws: string,
    id: string,
    nodeId: string,
    body: { action: "confirm" | "reject" | "needs_review" | "annotate" | "rerun"; note?: string },
  ) => api.post<T.Investigation>(`${w(ws)}/investigations/${e(id)}/nodes/${e(nodeId)}/actions`, body),
  saveFinding: (ws: string, id: string, nodeId: string, body: { statement?: string; notes?: string } = {}) =>
    api.post<T.Finding>(`${w(ws)}/investigations/${e(id)}/nodes/${e(nodeId)}/finding`, body),
  command: (ws: string, id: string | null, body: { text: string; node_id?: string | null }) =>
    api.post<T.CommandResult>(
      id ? `${w(ws)}/investigations/${e(id)}/command` : `${w(ws)}/investigations/command`,
      body,
    ),
};

export const artifacts = {
  get: (ws: string, id: string) => api.get<T.ArtifactRecord>(`${w(ws)}/artifacts/${e(id)}`),
  list: (ws: string, params: { kind?: string; investigation_id?: string; limit?: number } = {}) =>
    list(api.get<List<T.ArtifactRecord>>(`${w(ws)}/artifacts`, params)),
  lineage: (ws: string, id: string) => api.get<T.LineageGraph>(`${w(ws)}/artifacts/${e(id)}/lineage`),
  rerun: (ws: string, id: string) => api.post<T.ArtifactRecord>(`${w(ws)}/artifacts/${e(id)}/rerun`),
};

/* ------------------------------------------------------------------ findings */

export const findings = {
  list: (ws: string, params: { status?: string; statement_type?: string; investigation_id?: string; q?: string; limit?: number } = {}) =>
    list(api.get<List<T.Finding>>(`${w(ws)}/findings`, params)),
  get: (ws: string, id: string) => api.get<T.Finding>(`${w(ws)}/findings/${e(id)}`),
  create: (ws: string, body: Partial<T.Finding> & { statement: string }) => api.post<T.Finding>(`${w(ws)}/findings`, body),
  update: (ws: string, id: string, body: Partial<Pick<T.Finding, "statement" | "notes" | "business_impact" | "statement_type" | "tags">> & { change_note?: string }) =>
    api.patch<T.Finding>(`${w(ws)}/findings/${e(id)}`, body),
  setStatus: (ws: string, id: string, status: T.FindingStatus, note?: string) =>
    api.post<T.Finding>(`${w(ws)}/findings/${e(id)}/status`, { status, note }),
  remove: (ws: string, id: string) => api.del(`${w(ws)}/findings/${e(id)}`),
  comments: (ws: string, id: string) => list(api.get<List<T.FindingComment>>(`${w(ws)}/findings/${e(id)}/comments`)),
  addComment: (ws: string, id: string, body: string) =>
    api.post<T.FindingComment>(`${w(ws)}/findings/${e(id)}/comments`, { body }),
  deleteComment: (ws: string, id: string, commentId: string) =>
    api.del(`${w(ws)}/findings/${e(id)}/comments/${e(commentId)}`),
  versions: (ws: string, id: string) => list(api.get<List<T.FindingVersion>>(`${w(ws)}/findings/${e(id)}/versions`)),
  lineage: (ws: string, id: string) => api.get<T.LineageGraph>(`${w(ws)}/findings/${e(id)}/lineage`),
  artifacts: (ws: string, id: string) => list(api.get<List<T.ArtifactRecord>>(`${w(ws)}/findings/${e(id)}/artifacts`)),
};

