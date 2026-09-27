"use client";
import * as React from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import type { Finding } from "@/lib/api/types";
import { findings } from "@/lib/api/endpoints";
import { qk } from "@/lib/api/keys";
import { useWorkspace } from "@/components/providers/workspace";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { InlineError } from "@/components/states/states";
import { StatusBadge } from "@/components/analysis/status";
import { allowedTransitions, STATUS_STEPS, type Transition } from "./workflow";
import { cn } from "@/lib/utils";

/** Status stepper + transition buttons for the finding review workflow. */
export function FindingStatusControl({ finding, canEdit }: { finding: Finding; canEdit: boolean }) {
  const { id: ws } = useWorkspace();
  const qc = useQueryClient();
  const [pending, setPending] = React.useState<Transition | null>(null);
  const [note, setNote] = React.useState("");
  const mut = useMutation({
    mutationFn: ({ to, note }: { to: Transition["to"]; note?: string }) => findings.setStatus(ws, finding.id, to, note),
    onSuccess: (f) => {
      qc.setQueryData(qk.finding(ws, f.id), f);
      void qc.invalidateQueries({ queryKey: ["ws", ws, "findings"] });
      setPending(null);
      setNote("");
      toast.success(`Finding marked ${f.status.replace("_", " ")}`);
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const transitions = allowedTransitions(finding.status);
  const stepIdx = STATUS_STEPS.indexOf(finding.status);
  return (
    <div className="flex flex-col gap-2">
      <ol className="flex items-center gap-1 text-2xs" aria-label="Review progress">
        {STATUS_STEPS.map((s, i) => (
          <li key={s} className="flex items-center gap-1">
            <span
              className={cn(
                "rounded-sm px-1.5 py-0.5",
                finding.status === "rejected" ? "text-fg-faint" : i <= stepIdx ? "bg-accent-soft text-accent-soft-fg" : "text-fg-subtle",
                s === finding.status && "font-semibold",
              )}
            >
              {s === "needs_review" ? "Review" : s === "draft" ? "Draft" : "Confirmed"}
            </span>
            {i < STATUS_STEPS.length - 1 ? <span className="text-fg-faint">›</span> : null}
          </li>
        ))}
        {finding.status === "rejected" ? <StatusBadge status="rejected" className="ml-1" /> : null}
      </ol>
      {canEdit ? (
        <div className="flex flex-wrap gap-1.5">
          {transitions.map((t) => (
            <Button
              key={t.to}
              size="xs"
              variant={t.tone === "primary" ? "primary" : t.tone === "danger" ? "danger-ghost" : "secondary"}
              disabled={mut.isPending}
              onClick={() => (t.requiresNote ? setPending(t) : mut.mutate({ to: t.to }))}
            >
              {t.label}
            </Button>
          ))}
        </div>
      ) : null}
      <Dialog open={!!pending} onOpenChange={(o) => !o && setPending(null)}>
        <DialogContent size="sm">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (pending && note.trim()) mut.mutate({ to: pending.to, note: note.trim() });
            }}
          >
            <DialogHeader>
              <DialogTitle>{pending?.label}</DialogTitle>
              <DialogDescription>The note is kept in the finding&apos;s history and posted as a comment.</DialogDescription>
            </DialogHeader>
            <DialogBody>
              <Field label={pending?.to === "rejected" ? "Why is this finding rejected?" : "Reason"} htmlFor="status-note">
                <Textarea id="status-note" autoFocus rows={3} value={note} onChange={(e) => setNote(e.target.value)} />
              </Field>
              <InlineError error={mut.error} className="mt-2" />
            </DialogBody>
            <DialogFooter>
              <Button onClick={() => setPending(null)}>Cancel</Button>
              <Button type="submit" variant={pending?.to === "rejected" ? "danger" : "primary"} disabled={!note.trim() || mut.isPending}>
                {pending?.label}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
