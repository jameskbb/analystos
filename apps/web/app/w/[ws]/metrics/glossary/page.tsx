"use client";
import * as React from "react";
import { Suspense } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AlertTriangle, BookOpen, Pencil, Plus, Search, Trash2 } from "lucide-react";
import type { GlossaryTerm } from "@/lib/api/types";
import { glossary as glossaryApi, metrics as metricsApi } from "@/lib/api/resources/semantic";
import { qk } from "@/lib/api/keys";
import { useWorkspace } from "@/components/providers/workspace";
import { Page, PageBody, PageHeader } from "@/components/shell/page";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { ConfirmDialog } from "@/components/ui/alert-dialog";
import { EmptyState, LoadingState, QueryState } from "@/components/states/states";
import { LoadDemoButton } from "@/components/shell/use-load-demo";
import { SemanticNav } from "@/components/semantic/semantic-nav";
import { GlossaryDialog } from "@/components/semantic/semantic-dialogs";
import { findSynonymConflicts, termCandidates } from "@/components/semantic/metric-utils";
import { cn } from "@/lib/utils";

function GlossaryScreen() {
  const { id: ws, href, canEdit } = useWorkspace();
  const qc = useQueryClient();
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const [q, setQ] = React.useState(params.get("q") ?? "");
  const [onlyAmbiguous, setOnlyAmbiguous] = React.useState(false);
  const [editing, setEditing] = React.useState<GlossaryTerm | null>(null);
  const [creating, setCreating] = React.useState(params.get("new") === "1");
  const [deleting, setDeleting] = React.useState<GlossaryTerm | null>(null);
  const terms = useQuery({ queryKey: qk.glossary(ws), queryFn: () => glossaryApi.list(ws) });
  const metrics = useQuery({ queryKey: qk.metrics(ws), queryFn: () => metricsApi.list(ws) });
  const conflicts = React.useMemo(() => findSynonymConflicts(metrics.data ?? []), [metrics.data]);
  const labelOf = (id: string) => metrics.data?.find((m) => m.id === id)?.label ?? id;

  React.useEffect(() => {
    const t = setTimeout(() => {
      if ((params.get("q") ?? "") !== q) router.replace(`${pathname}${q ? `?q=${encodeURIComponent(q)}` : ""}`, { scroll: false });
    }, 250);
    return () => clearTimeout(t);
  }, [q, params, pathname, router]);

  const del = useMutation({
    mutationFn: (id: string) => glossaryApi.remove(ws, id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.glossary(ws) });
      setDeleting(null);
      toast.success("Term deleted");
    },
    onError: (e: Error) => toast.error("Could not delete the term", { description: e.message }),
  });

  return (
    <Page>
      <PageHeader
        title="Business glossary"
        description="Shared definitions AnalystOS uses to interpret questions. Ambiguous terms make it ask instead of guess."
        meta={<SemanticNav />}
        actions={
          <>
            <div className="relative">
              <Search className="pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-fg-faint" />
              <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search terms…" className="w-52 pl-7" aria-label="Search glossary" />
            </div>
            <label className="flex items-center gap-1.5 text-sm text-fg-muted">
              <input type="checkbox" checked={onlyAmbiguous} onChange={(e) => setOnlyAmbiguous(e.target.checked)} />
              Ambiguous only
            </label>
            {canEdit ? (
              <Button variant="primary" onClick={() => setCreating(true)}>
                <Plus /> New term
              </Button>
            ) : null}
          </>
        }
      />
      <PageBody>
        <QueryState
          query={terms}
          empty={
            <EmptyState
              icon={BookOpen}
              title="The glossary is empty"
              description="Define business terms (e.g. “margin”, “active customer”) so questions are interpreted against your definitions."
              action={<LoadDemoButton workspaceId={ws} />}
              secondary={
                canEdit ? (
                  <Button onClick={() => setCreating(true)}>
                    <Plus /> Add a term
                  </Button>
                ) : null
              }
            />
          }
        >
          {(list) => {
            const needle = q.trim().toLowerCase();
            const rows = list
              .map((t) => ({ t, candidates: termCandidates(t, conflicts) }))
              .filter(({ t }) =>
                !needle
                  ? true
                  : [t.term, t.definition, t.formula ?? "", t.metric_id ?? "", ...(t.synonyms ?? []), ...(t.related ?? [])]
                      .join(" ")
                      .toLowerCase()
                      .includes(needle),
              )
              .filter(({ candidates }) => !onlyAmbiguous || candidates.length > 1)
              .sort((a, b) => a.t.term.localeCompare(b.t.term));
            if (!rows.length)
              return <EmptyState compact icon={Search} title="No matching terms" description={needle ? `Nothing matches “${q}”.` : undefined} />;
            return (
              <ul className="grid gap-2 lg:grid-cols-2" aria-label="Glossary terms">
                {rows.map(({ t, candidates }) => {
                  const ambiguous = candidates.length > 1;
                  const highlighted = needle && t.term.toLowerCase() === needle;
                  return (
                    <li
                      key={t.id}
                      className={cn(
                        "flex flex-col gap-1.5 rounded-md border bg-bg p-3",
                        ambiguous ? "border-warning/40" : "border-border",
                        highlighted && "shadow-[0_0_0_1px_var(--accent)]",
                      )}
                    >
                      <div className="flex items-start gap-2">
                        <h2 className="min-w-0 flex-1 text-sm font-semibold">{t.term}</h2>
                        {ambiguous ? (
                          <Badge tone="warning">
                            <AlertTriangle /> Ambiguous
                          </Badge>
                        ) : null}
                        {canEdit ? (
                          <span className="-mt-1 -mr-1 flex">
                            <Button size="icon-xs" variant="ghost" onClick={() => setEditing(t)} aria-label={`Edit ${t.term}`}>
                              <Pencil />
                            </Button>
                            <Button size="icon-xs" variant="ghost" onClick={() => setDeleting(t)} aria-label={`Delete ${t.term}`}>
                              <Trash2 />
                            </Button>
                          </span>
                        ) : null}
                      </div>
                      <p className="text-sm text-fg-muted">{t.definition}</p>
                      {t.formula ? <code className="w-fit rounded-sm bg-bg-muted px-1.5 py-0.5 font-mono text-xs">{t.formula}</code> : null}
                      {ambiguous ? (
                        <div className="text-xs text-warning">
                          Could mean:{" "}
                          {candidates.map((c, i) => (
                            <span key={c}>
                              {i ? ", " : ""}
                              <Link href={href(`/metrics/${encodeURIComponent(c)}`)} className="underline underline-offset-2">
                                {labelOf(c)}
                              </Link>
                            </span>
                          ))}
                          . Questions using this term will ask which one is meant.
                        </div>
                      ) : t.metric_id ? (
                        <div className="text-xs text-fg-subtle">
                          Metric:{" "}
                          <Link href={href(`/metrics/${encodeURIComponent(t.metric_id)}`)} className="text-accent hover:underline">
                            {labelOf(t.metric_id)}
                          </Link>
                        </div>
                      ) : null}
                      {t.synonyms?.length || t.related?.length ? (
                        <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-fg-subtle">
                          {t.synonyms?.length ? <span>Synonyms: {t.synonyms.join(", ")}</span> : null}
                          {t.related?.length ? (
                            <span className="flex flex-wrap items-center gap-1">
                              Related:
                              {t.related.map((r) => (
                                <button key={r} type="button" className="text-accent hover:underline" onClick={() => setQ(r)}>
                                  {r}
                                </button>
                              ))}
                            </span>
                          ) : null}
                        </div>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            );
          }}
        </QueryState>
      </PageBody>
      <GlossaryDialog
        open={creating || !!editing}
        onOpenChange={(o) => {
          if (!o) {
            setCreating(false);
            setEditing(null);
          }
        }}
        term={editing}
      />
      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Delete “${deleting?.term}”?`}
        description="Question interpretation will no longer recognize this term."
        confirmLabel="Delete"
        destructive
        pending={del.isPending}
        onConfirm={() => deleting && del.mutate(deleting.id)}
      />
    </Page>
  );
}

export default function GlossaryPage() {
  return (
    <Suspense fallback={<LoadingState variant="block" />}>
      <GlossaryScreen />
    </Suspense>
  );
}
