"use client";
import * as React from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, MessageSquareText } from "lucide-react";
import { investigations } from "@/lib/api/endpoints";
import { qk } from "@/lib/api/keys";
import { useWorkspace } from "@/components/providers/workspace";
import { Button } from "@/components/ui/button";
import { NativeSelect, Textarea } from "@/components/ui/input";
import { InlineError } from "@/components/states/states";
import { Kbd } from "@/components/ui/kbd";
import { cn } from "@/lib/utils";

export const EXAMPLE_QUESTIONS = [
  "Why was August revenue down?",
  "Revenue was roughly flat. Why did margin decline?",
  "Which region drove the change in revenue last month?",
  "Why did conversion rate fall in Q3?",
  "What caused the forecast miss in August?",
];

/**
 * Ask composer: a question becomes an investigation (interpret → plan → run).
 * Expensive plans stop for approval; lightweight questions run immediately.
 */
export function AskComposer({ autoFocus = false, className }: { autoFocus?: boolean; className?: string }) {
  const { id: ws, href, canEdit } = useWorkspace();
  const router = useRouter();
  const qc = useQueryClient();
  const [question, setQuestion] = React.useState("");
  const [template, setTemplate] = React.useState("");
  const templates = useQuery({ queryKey: qk.templates(ws), queryFn: () => investigations.templates(ws), retry: false });
  const create = useMutation({
    mutationFn: (q: string) => investigations.create(ws, { question: q, template: template || null, auto_run: true }),
    onSuccess: (inv) => {
      void qc.invalidateQueries({ queryKey: ["ws", ws, "investigations"] });
      router.push(href(`/investigate/${inv.id}`));
    },
  });
  const submit = (q = question) => {
    const v = q.trim();
    if (v && !create.isPending) create.mutate(v);
  };
  const tmpl = (templates.data ?? []).find((t) => t.id === template);
  return (
    <form
      className={cn("flex flex-col gap-2 rounded-md border border-border bg-bg p-3", className)}
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <label htmlFor="ask-question" className="flex items-center gap-1.5 text-sm font-medium">
        <MessageSquareText className="size-4 text-fg-subtle" /> Ask a business question
      </label>
      <Textarea
        id="ask-question"
        autoFocus={autoFocus}
        rows={2}
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey || !e.shiftKey)) {
            e.preventDefault();
            submit();
          }
        }}
        placeholder="Why was August revenue down?"
        className="text-base"
        disabled={!canEdit}
      />
      <div className="flex flex-wrap items-center gap-2">
        {templates.data?.length ? (
          <label className="flex items-center gap-1.5 text-xs text-fg-subtle">
            Template
            <NativeSelect className="h-7 w-52" value={template} onChange={(e) => setTemplate(e.target.value)} aria-label="Investigation template">
              <option value="">Detect from question</option>
              {templates.data.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </NativeSelect>
          </label>
        ) : null}
        <span className="ml-auto hidden items-center gap-1 text-2xs text-fg-subtle sm:flex">
          <Kbd>↵</Kbd> investigate · <Kbd>⇧</Kbd>
          <Kbd>↵</Kbd> new line
        </span>
        <Button type="submit" variant="primary" disabled={!question.trim() || create.isPending || !canEdit}>
          {create.isPending ? "Interpreting…" : "Investigate"} <ArrowRight />
        </Button>
      </div>
      {tmpl ? <p className="text-xs text-fg-subtle">{tmpl.description}</p> : null}
      <InlineError error={create.error} />
      <div className="flex flex-wrap gap-1 pt-1" aria-label="Example questions">
        {EXAMPLE_QUESTIONS.map((q) => (
          <button
            key={q}
            type="button"
            onClick={() => {
              setQuestion(q);
              submit(q);
            }}
            disabled={create.isPending || !canEdit}
            className="h-6 rounded-sm border border-border bg-bg-subtle px-2 text-xs text-fg-muted hover:border-border-strong hover:text-fg"
          >
            {q}
          </button>
        ))}
      </div>
    </form>
  );
}
