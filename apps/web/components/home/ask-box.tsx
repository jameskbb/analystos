"use client";
import * as React from "react";
import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { ArrowRight, MessageSquareText } from "lucide-react";
import { investigations } from "@/lib/api/endpoints";
import { Button } from "@/components/ui/button";
import { Kbd } from "@/components/ui/kbd";
import { Spinner } from "@/components/ui/spinner";
import { InlineError } from "@/components/states/states";

/**
 * The home "Ask AnalystOS" entry. Submitting creates an investigation (interpreted and planned by the
 * engine) and opens the investigation workstation; nothing is answered in free prose here.
 */
export function AskBox({
  workspaceId,
  href,
  examples,
}: {
  workspaceId: string;
  href: (p: string) => string;
  examples: string[];
}) {
  const router = useRouter();
  const [q, setQ] = React.useState("");
  const ask = useMutation({
    mutationFn: (question: string) => investigations.create(workspaceId, { question }),
    onSuccess: (inv) => router.push(href(`/investigate/${inv.id}`)),
  });
  const submit = (text: string) => {
    const t = text.trim();
    if (t.length >= 3 && !ask.isPending) ask.mutate(t);
  };
  return (
    <section aria-labelledby="ask-title" className="rounded-md border border-border bg-bg p-3">
      <h2 id="ask-title" className="mb-2 flex items-center gap-1.5 text-sm font-semibold">
        <MessageSquareText className="size-3.5 text-accent" aria-hidden /> Ask AnalystOS
      </h2>
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          submit(q);
        }}
      >
        <label htmlFor="home-ask" className="sr-only">
          Ask a business question
        </label>
        <input
          id="home-ask"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Why was August revenue down?"
          className="h-9 min-w-0 flex-1 rounded border border-border-strong bg-bg px-3 text-base placeholder:text-fg-faint focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-ring"
          autoComplete="off"
        />
        <Button type="submit" variant="primary" size="md" className="h-9" disabled={q.trim().length < 3 || ask.isPending}>
          {ask.isPending ? <Spinner className="text-accent-fg" /> : <ArrowRight />}
          Investigate
        </Button>
      </form>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <span className="text-xs text-fg-subtle">Try:</span>
        {examples.map((ex) => (
          <button
            key={ex}
            type="button"
            onClick={() => {
              setQ(ex);
              submit(ex);
            }}
            disabled={ask.isPending}
            className="h-6 rounded-sm border border-border bg-bg-subtle px-2 text-xs text-fg-muted hover:border-border-strong hover:text-fg disabled:opacity-50"
          >
            {ex}
          </button>
        ))}
        <span className="ml-auto hidden items-center gap-1 text-2xs text-fg-faint sm:flex">
          <Kbd>N</Kbd> anywhere starts a new investigation
        </span>
      </div>
      {ask.error ? <InlineError error={ask.error} className="mt-2" /> : null}
      <p className="mt-2 text-2xs text-fg-subtle">
        AnalystOS builds a plan and runs real queries; every number in the result links to the SQL that produced it.
      </p>
    </section>
  );
}
