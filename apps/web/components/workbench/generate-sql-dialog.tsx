"use client";
import * as React from "react";
import Link from "next/link";
import { useMutation } from "@tanstack/react-query";
import { queries, type GeneratedSql } from "@/lib/api/resources/workbench";
import { ApiError } from "@/lib/api/client";
import { useWorkspace } from "@/components/providers/workspace";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { CodeBlock } from "@/components/code/code-block";
import { InlineError } from "@/components/states/states";
import { Spinner } from "@/components/ui/spinner";

/**
 * Drafts SQL from a prompt with the configured AI provider. The draft is validated read-only by the
 * API and only inserted into the editor; it never runs automatically.
 */
export function GenerateSqlDialog({
  open,
  onOpenChange,
  onInsert,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onInsert: (sql: string) => void;
}) {
  const { id: ws, href } = useWorkspace();
  const [prompt, setPrompt] = React.useState("");
  const gen = useMutation<GeneratedSql, Error>({ mutationFn: () => queries.generate(ws, prompt.trim()) });
  const disabled = gen.error instanceof ApiError && gen.error.code === "ai_disabled";
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        onOpenChange(o);
        if (!o) gen.reset();
      }}
    >
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>Draft SQL from a description</DialogTitle>
          <DialogDescription>
            The draft goes into the editor for you to review and edit. It is never run automatically.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-3">
          <Textarea
            aria-label="Describe the query"
            autoFocus
            rows={3}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="Monthly net revenue by branch for 2026, largest first"
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && prompt.trim()) gen.mutate();
            }}
          />
          {disabled ? (
            <div role="alert" className="rounded border border-border bg-bg-subtle px-3 py-2 text-sm">
              No AI provider is configured for this workspace, so SQL drafting is unavailable. Everything else in the SQL
              workspace works without one.{" "}
              <Link className="text-accent hover:underline" href={href("/settings?tab=ai")}>
                Configure an AI provider
              </Link>
            </div>
          ) : (
            <InlineError error={gen.error} />
          )}
          {gen.data ? (
            <div className="flex flex-col gap-1.5">
              <CodeBlock code={gen.data.sql} maxHeight={260} />
              {gen.data.explanation ? <p className="text-sm text-fg-muted">{gen.data.explanation}</p> : null}
              {gen.data.executable === false ? (
                <p role="alert" className="text-xs text-negative">
                  This draft did not run after {gen.data.attempts?.length ?? 1} attempt(s); edit it before running.
                </p>
              ) : gen.data.repaired ? (
                <p className="text-xs text-warning">The first draft failed and was repaired; the final version ran.</p>
              ) : null}
              {gen.data.attempts && gen.data.attempts.length > 1 ? (
                <details className="text-xs">
                  <summary className="cursor-pointer text-fg-subtle">Attempts ({gen.data.attempts.length})</summary>
                  <ol className="mt-1 flex flex-col gap-1 pl-4">
                    {gen.data.attempts.map((a) => (
                      <li key={a.attempt} className={a.ok ? "text-positive" : "text-negative"}>
                        Attempt {a.attempt}: {a.ok ? `ran (${a.row_count ?? 0} rows in the dry run)` : a.error}
                      </li>
                    ))}
                  </ol>
                </details>
              ) : null}
              {gen.data.model ? <p className="text-2xs text-fg-faint">Drafted by {gen.data.model}</p> : null}
            </div>
          ) : null}
        </DialogBody>
        <DialogFooter>
          <Button variant="secondary" disabled={!prompt.trim() || gen.isPending} onClick={() => gen.mutate()}>
            {gen.isPending ? <Spinner /> : null} {gen.data ? "Regenerate" : "Draft SQL"}
          </Button>
          <Button
            variant="primary"
            disabled={!gen.data}
            onClick={() => {
              if (gen.data) onInsert(gen.data.sql);
              onOpenChange(false);
              gen.reset();
            }}
          >
            Insert into editor
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
