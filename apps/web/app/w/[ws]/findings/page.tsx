"use client";
import * as React from "react";
import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Lightbulb } from "lucide-react";
import { findings, investigations } from "@/lib/api/endpoints";
import { qk } from "@/lib/api/keys";
import { useWorkspace } from "@/components/providers/workspace";
import { Page, PageBody, PageHeader } from "@/components/shell/page";
import { FindingRow } from "@/components/findings/finding-row";
import { Input, NativeSelect } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { EmptyState, ErrorState, LoadingState } from "@/components/states/states";
import { formatInt } from "@/lib/format";

function FindingsList() {
  const { id: ws, href } = useWorkspace();
  const router = useRouter();
  const params = useSearchParams();
  const status = params.get("status") ?? "";
  const type = params.get("type") ?? "";
  const inv = params.get("investigation") ?? "";
  const [q, setQ] = React.useState(params.get("q") ?? "");
  const [debounced, setDebounced] = React.useState(q);
  React.useEffect(() => {
    const t = setTimeout(() => setDebounced(q.trim()), 250);
    return () => clearTimeout(t);
  }, [q]);

  const setParam = (k: string, v: string) => {
    const next = new URLSearchParams(params.toString());
    if (v) next.set(k, v);
    else next.delete(k);
    router.replace(`?${next.toString()}`);
  };

  const filters = { status, statement_type: type, investigation_id: inv, q: debounced };
  const list = useQuery({
    queryKey: qk.findings(ws, filters),
    queryFn: () =>
      findings.list(ws, {
        status: status || undefined,
        statement_type: type || undefined,
        investigation_id: inv || undefined,
        q: debounced || undefined,
        limit: 500,
      }),
  });
  const invs = useQuery({ queryKey: qk.investigations(ws, { status: "" }), queryFn: () => investigations.list(ws, { limit: 200 }) });
  const text = debounced.toLowerCase();
  const items = (list.data ?? []).filter((f) => !text || f.statement.toLowerCase().includes(text));
  const counts = (list.data ?? []).reduce<Record<string, number>>((acc, f) => ({ ...acc, [f.status]: (acc[f.status] ?? 0) + 1 }), {});
  const filtered = !!(status || type || inv || debounced);

  return (
    <Page>
      <PageHeader
        title="Findings"
        description="Results promoted from investigations, each backed by executed queries and reviewed before it is reported."
        meta={
          list.data ? (
            <>
              <span className="tabular">{formatInt(counts.confirmed ?? 0)} confirmed</span>
              <span className="tabular">{formatInt(counts.needs_review ?? 0)} need review</span>
              <span className="tabular">{formatInt(counts.draft ?? 0)} draft</span>
              <span className="tabular">{formatInt(counts.rejected ?? 0)} rejected</span>
            </>
          ) : null
        }
      />
      <PageBody className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2" role="search">
          <Input className="h-7 w-72" placeholder="Search statements…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search findings" />
          <NativeSelect className="h-7 w-40" value={status} onChange={(e) => setParam("status", e.target.value)} aria-label="Status">
            <option value="">All statuses</option>
            <option value="draft">Draft</option>
            <option value="needs_review">Needs review</option>
            <option value="confirmed">Confirmed</option>
            <option value="rejected">Rejected</option>
          </NativeSelect>
          <NativeSelect className="h-7 w-48" value={type} onChange={(e) => setParam("type", e.target.value)} aria-label="Statement type">
            <option value="">All statement types</option>
            <option value="observation">Observations</option>
            <option value="supported_explanation">Supported explanations</option>
            <option value="hypothesis">Hypotheses</option>
          </NativeSelect>
          <NativeSelect className="h-7 w-64" value={inv} onChange={(e) => setParam("investigation", e.target.value)} aria-label="Investigation">
            <option value="">All investigations</option>
            {(invs.data ?? []).map((i) => (
              <option key={i.id} value={i.id}>
                {i.question}
              </option>
            ))}
          </NativeSelect>
          {filtered ? (
            <Button
              variant="ghost"
              size="xs"
              onClick={() => {
                setQ("");
                router.replace("?");
              }}
            >
              Clear filters
            </Button>
          ) : null}
        </div>
        {list.isLoading ? (
          <LoadingState />
        ) : list.error ? (
          <ErrorState error={list.error} onRetry={() => void list.refetch()} />
        ) : items.length === 0 ? (
          filtered ? (
            <EmptyState compact title="No findings match these filters" />
          ) : (
            <EmptyState
              icon={Lightbulb}
              title="No findings yet"
              description="Run an investigation and save a node as a finding. Findings keep their queries, filters and metric versions so they can be reproduced."
              action={
                <Button variant="primary" onClick={() => router.push(href("/investigate?new=1"))}>
                  Start an investigation
                </Button>
              }
            />
          )
        ) : (
          <ul className="rounded-md border border-border" aria-label="Findings">
            {items.map((f) => (
              <FindingRow key={f.id} finding={f} />
            ))}
          </ul>
        )}
      </PageBody>
    </Page>
  );
}

export default function FindingsPage() {
  return (
    <Suspense fallback={<LoadingState variant="block" className="h-full" />}>
      <FindingsList />
    </Suspense>
  );
}
