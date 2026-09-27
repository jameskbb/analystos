"use client";
import * as React from "react";
import { Check, Copy, KeyRound } from "lucide-react";
import { Button } from "@/components/ui/button";

/**
 * Shows a newly created API token exactly once. The raw token lives only in this component's props;
 * dismissing it discards the value for good (the server stores only a hash).
 */
export function TokenReveal({ token, name, onDone }: { token: string; name: string; onDone: () => void }) {
  const [copied, setCopied] = React.useState(false);
  const [copyError, setCopyError] = React.useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(token);
      setCopied(true);
      setCopyError(false);
    } catch {
      setCopyError(true);
    }
  };
  return (
    <div role="alert" className="rounded-md border border-warning/40 bg-warning-soft p-3">
      <div className="flex items-center gap-1.5 text-sm font-semibold text-warning">
        <KeyRound className="size-3.5" aria-hidden /> Copy the token for &ldquo;{name}&rdquo; now
      </div>
      <p className="mt-0.5 text-xs text-fg-muted">
        This is the only time it is shown. AnalystOS stores a hash, so it cannot be recovered; revoke it and create a
        new one if it is lost.
      </p>
      <div className="mt-2 flex items-center gap-2">
        <code
          data-testid="raw-token"
          className="min-w-0 flex-1 truncate rounded border border-border-strong bg-bg px-2 py-1 font-mono text-xs select-all"
        >
          {token}
        </code>
        <Button size="sm" onClick={() => void copy()} aria-label="Copy token">
          {copied ? <Check className="text-positive" /> : <Copy />}
          {copied ? "Copied" : "Copy"}
        </Button>
        <Button size="sm" variant="primary" onClick={onDone}>
          Done
        </Button>
      </div>
      {copyError ? <p className="mt-1 text-xs text-negative">Clipboard unavailable: select the token and copy it manually.</p> : null}
    </div>
  );
}
