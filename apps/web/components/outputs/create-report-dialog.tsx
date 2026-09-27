"use client";
import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { investigations as investigationsApi } from "@/lib/api/endpoints";
import { reports } from "@/lib/api/resources/outputs";
import { qk } from "@/lib/api/keys";
import { useWorkspace } from "@/components/providers/workspace";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input, NativeSelect } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { InlineError } from "@/components/states/states";
import { cn } from "@/lib/utils";
import { newBlock } from "./model";

type Mode = "blank" | "investigation" | "summary";

const MODES: { value: Mode; title: string; description: string }[] = [
  { value: "blank", title: "Blank report", description: "Start from a heading and a narrative block; add findings, KPIs and charts." },
  { value: "investigation", title: "From an investigation", description: "Question, findings tree, charts, methodology and sources from one investigation." },
  { value: "summary", title: "Executive summary", description: "Built only from confirmed findings, separating facts, explanations and hypotheses." },
];

export function CreateReportDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const { id: ws, href } = useWorkspace();
  const router = useRouter();
  const qc = useQueryClient();
  const [mode, setMode] = React.useState<Mode>("blank");
  const [title, setTitle] = React.useState("");
  const [investigationId, setInvestigationId] = React.useState("");
  const invs = useQuery({
    queryKey: qk.investigations(ws, { for: "reports" }),
    queryFn: () => investigationsApi.list(ws, { limit: 100 }),
    enabled: open && mode === "investigation",
  });
  const create = useMutation({
    mutationFn: async () => {
      if (mode === "investigation") return reports.fromInvestigation(ws, investigationId);
      if (mode === "summary") return reports.executiveSummary(ws, { title: title.trim() || "Executive summary" });
      const heading = { ...newBlock("heading"), text: title.trim() || "Untitled report" };
      return reports.create(ws, { title: title.trim() || "Untitled report", kind: "custom", blocks: [heading, newBlock("narrative")] });
    },
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: qk.reports(ws) });
      onOpenChange(false);
      router.push(href(`/reports/${r.id}`));
    },
  });
  const valid = mode === "investigation" ? !!investigationId : mode === "blank" ? title.trim().length > 0 : true;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="md">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (valid) create.mutate();
          }}
        >
          <DialogHeader>
            <DialogTitle>New report</DialogTitle>
            <DialogDescription>
              For a monthly business review, use{" "}
              <Link className="text-accent hover:underline" href={href("/reports/review")} onClick={() => onOpenChange(false)}>
                Business review mode
              </Link>
              .
            </DialogDescription>
          </DialogHeader>
          <DialogBody className="flex flex-col gap-3">
            <div role="radiogroup" aria-label="Report type" className="grid gap-1.5">
              {MODES.map((m) => (
                <button
                  key={m.value}
                  type="button"
                  role="radio"
                  aria-checked={mode === m.value}
                  onClick={() => setMode(m.value)}
                  className={cn(
                    "rounded border border-border px-3 py-2 text-left hover:bg-bg-subtle",
                    mode === m.value && "border-accent bg-accent-soft/40 shadow-[0_0_0_1px_var(--accent)]",
                  )}
                >
                  <div className="text-sm font-medium">{m.title}</div>
                  <div className="text-xs text-fg-subtle">{m.description}</div>
                </button>
              ))}
            </div>
            {mode === "investigation" ? (
              <Field label="Investigation" htmlFor="report-inv">
                {invs.isError ? (
                  <InlineError error={invs.error} />
                ) : (
                  <NativeSelect id="report-inv" value={investigationId} onChange={(e) => setInvestigationId(e.target.value)}>
                    <option value="">{invs.isLoading ? "Loading…" : invs.data?.length ? "Choose an investigation…" : "No investigations yet"}</option>
                    {(invs.data ?? []).map((i) => (
                      <option key={i.id} value={i.id}>
                        {i.title || i.question}
                      </option>
                    ))}
                  </NativeSelect>
                )}
              </Field>
            ) : (
              <Field label="Title" htmlFor="report-title">
                <Input
                  id="report-title"
                  autoFocus
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder={mode === "summary" ? "Executive summary" : "August revenue decline: analytical memo"}
                />
              </Field>
            )}
            <InlineError error={create.error} />
          </DialogBody>
          <DialogFooter>
            <Button variant="secondary" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" disabled={!valid || create.isPending}>
              Create report
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
