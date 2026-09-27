"use client";
import * as React from "react";
import { Check, Copy } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const SQL_KEYWORDS =
  /\b(select|from|where|group|by|order|having|limit|offset|join|left|right|inner|outer|full|cross|on|as|and|or|not|in|is|null|case|when|then|else|end|with|union|all|distinct|over|partition|sum|count|avg|min|max|coalesce|cast|date_trunc|between|like|ilike|desc|asc|nullif|filter|interval|extract|using|qualify|window|rows|range|lag|lead|round|abs)\b/gi;

function highlightSql(code: string): React.ReactNode[] {
  // Lightweight tokenization for read-only display (strings, comments, numbers, keywords).
  const out: React.ReactNode[] = [];
  const re = /('(?:[^']|'')*')|(--[^\n]*)|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*)|(\s+)|(.)/g;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(code))) {
    const [tok, str, comment, num, word] = m;
    if (str) out.push(<span key={i++} className="text-positive">{tok}</span>);
    else if (comment) out.push(<span key={i++} className="text-fg-faint italic">{tok}</span>);
    else if (num) out.push(<span key={i++} className="text-[var(--chart-2)]">{tok}</span>);
    else if (word && SQL_KEYWORDS.test(word)) {
      SQL_KEYWORDS.lastIndex = 0;
      out.push(<span key={i++} className="font-medium text-accent">{tok}</span>);
    } else {
      SQL_KEYWORDS.lastIndex = 0;
      out.push(tok);
    }
  }
  return out;
}

/** Read-only code display with copy. SQL gets lightweight highlighting. */
export function CodeBlock({
  code,
  language = "sql",
  className,
  maxHeight,
  copyable = true,
  title,
}: {
  code: string | null | undefined;
  language?: "sql" | "python" | "text" | "json" | "yaml";
  className?: string;
  maxHeight?: number | string;
  copyable?: boolean;
  title?: React.ReactNode;
}) {
  const [copied, setCopied] = React.useState(false);
  const text = code ?? "";
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      toast.error("Clipboard unavailable");
    }
  };
  return (
    <div className={cn("group relative min-w-0 rounded border border-border bg-code-bg", className)}>
      {title ? (
        <div className="flex h-7 items-center justify-between border-b border-border px-2 text-xs text-fg-subtle">
          <span className="truncate">{title}</span>
        </div>
      ) : null}
      {copyable && text ? (
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={copy}
          aria-label="Copy code"
          className={cn("absolute right-1 z-[1] bg-code-bg opacity-70 group-hover:opacity-100", title ? "top-8" : "top-1")}
        >
          {copied ? <Check className="text-positive" /> : <Copy />}
        </Button>
      ) : null}
      <pre
        className="overflow-auto px-3 py-2 font-mono text-xs leading-5 whitespace-pre scrollbar-thin"
        style={{ maxHeight }}
      >
        <code>{language === "sql" ? highlightSql(text) : text}</code>
      </pre>
    </div>
  );
}
