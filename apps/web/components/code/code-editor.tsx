"use client";
import * as React from "react";
import { EditorState, Compartment, type Extension } from "@codemirror/state";
import {
  EditorView,
  keymap,
  lineNumbers,
  highlightActiveLine,
  highlightActiveLineGutter,
  drawSelection,
  placeholder as placeholderExt,
} from "@codemirror/view";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { sql, PostgreSQL, type SQLNamespace } from "@codemirror/lang-sql";
import { python } from "@codemirror/lang-python";
import { markdown } from "@codemirror/lang-markdown";
import { autocompletion, closeBrackets, closeBracketsKeymap, completionKeymap } from "@codemirror/autocomplete";
import { bracketMatching, indentOnInput, syntaxHighlighting, HighlightStyle } from "@codemirror/language";
import { searchKeymap, highlightSelectionMatches } from "@codemirror/search";
import { tags as t } from "@lezer/highlight";
import { cn } from "@/lib/utils";

export type EditorLanguage = "sql" | "python" | "markdown" | "text";

export interface SchemaTable {
  name: string;
  schema?: string;
  columns: { name: string; type?: string }[];
}

/** Converts table metadata into a CodeMirror SQL namespace for column/table autocomplete. */
export function schemaToNamespace(tables: SchemaTable[]): SQLNamespace {
  const ns: Record<string, SQLNamespace> = {};
  for (const tbl of tables) {
    ns[tbl.name] = {
      self: { label: tbl.name, type: "type", detail: tbl.schema ? `${tbl.schema} table` : "table" },
      children: tbl.columns.map((c) => ({ label: c.name, type: "property", detail: c.type ?? "" })),
    };
  }
  return ns;
}

const highlight = HighlightStyle.define([
  { tag: [t.keyword, t.operatorKeyword, t.modifier], color: "var(--accent)", fontWeight: "500" },
  { tag: [t.string, t.special(t.string)], color: "var(--positive)" },
  { tag: [t.number, t.bool, t.null], color: "var(--chart-2)" },
  { tag: [t.comment, t.lineComment, t.blockComment], color: "var(--fg-faint)", fontStyle: "italic" },
  { tag: [t.function(t.variableName), t.function(t.propertyName)], color: "var(--chart-4)" },
  { tag: [t.typeName, t.className], color: "var(--chart-3)" },
  { tag: [t.heading], fontWeight: "600", color: "var(--fg)" },
  { tag: [t.emphasis], fontStyle: "italic" },
  { tag: [t.strong], fontWeight: "600" },
  { tag: [t.link, t.url], color: "var(--accent)" },
  { tag: [t.punctuation, t.separator, t.bracket], color: "var(--fg-subtle)" },
]);

const baseTheme = EditorView.theme({
  "&": { fontSize: "12.5px", backgroundColor: "var(--code-bg)", color: "var(--fg)", height: "100%" },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": { fontFamily: "var(--font-mono)", lineHeight: "1.55" },
  ".cm-content": { caretColor: "var(--fg)", padding: "6px 0" },
  ".cm-gutters": {
    backgroundColor: "var(--code-bg)",
    color: "var(--fg-faint)",
    border: "none",
    borderRight: "1px solid var(--border)",
  },
  ".cm-activeLine": { backgroundColor: "color-mix(in srgb, var(--bg-muted) 60%, transparent)" },
  ".cm-activeLineGutter": { backgroundColor: "transparent", color: "var(--fg-muted)" },
  ".cm-selectionBackground, &.cm-focused .cm-selectionBackground, ::selection": {
    backgroundColor: "var(--accent-soft) !important",
  },
  ".cm-cursor": { borderLeftColor: "var(--fg)" },
  ".cm-placeholder": { color: "var(--fg-faint)" },
  ".cm-tooltip": {
    backgroundColor: "var(--bg)",
    border: "1px solid var(--border-strong)",
    borderRadius: "4px",
    boxShadow: "var(--shadow-pop)",
  },
  ".cm-tooltip-autocomplete ul li[aria-selected]": { backgroundColor: "var(--accent-soft)", color: "var(--fg)" },
  ".cm-completionDetail": { color: "var(--fg-subtle)", fontStyle: "normal", marginLeft: "8px" },
  ".cm-matchingBracket": { backgroundColor: "var(--bg-muted)", outline: "1px solid var(--border-strong)" },
  ".cm-searchMatch": { backgroundColor: "var(--warning-soft)" },
});

/** DuckDB SQL is close to PostgreSQL; the Postgres dialect gives correct keywords and quoting. */
function languageExt(lang: EditorLanguage, schema?: SchemaTable[]): Extension {
  if (lang === "sql")
    return sql({
      dialect: PostgreSQL,
      schema: schema ? schemaToNamespace(schema) : undefined,
      upperCaseKeywords: false,
    });
  if (lang === "python") return python();
  if (lang === "markdown") return markdown();
  return [];
}

export interface CodeEditorHandle {
  focus: () => void;
  getSelection: () => string;
  insert: (text: string) => void;
}

export const CodeEditor = React.forwardRef<
  CodeEditorHandle,
  {
    value: string;
    onChange?: (value: string) => void;
    language?: EditorLanguage;
    schema?: SchemaTable[];
    onRun?: () => void;
    readOnly?: boolean;
    placeholder?: string;
    className?: string;
    minHeight?: number;
    maxHeight?: number;
    lineNumbers?: boolean;
    ariaLabel?: string;
    autoFocus?: boolean;
  }
>(function CodeEditor(
  {
    value,
    onChange,
    language = "sql",
    schema,
    onRun,
    readOnly = false,
    placeholder,
    className,
    minHeight,
    maxHeight,
    lineNumbers: showLines = true,
    ariaLabel = "Code editor",
    autoFocus = false,
  },
  ref,
) {
  const host = React.useRef<HTMLDivElement>(null);
  const view = React.useRef<EditorView | null>(null);
  const langComp = React.useRef(new Compartment());
  const roComp = React.useRef(new Compartment());
  const onChangeRef = React.useRef(onChange);
  const onRunRef = React.useRef(onRun);
  onChangeRef.current = onChange;
  onRunRef.current = onRun;

  React.useEffect(() => {
    if (!host.current) return;
    const runKey = keymap.of([
      {
        key: "Mod-Enter",
        preventDefault: true,
        run: () => {
          onRunRef.current?.();
          return !!onRunRef.current;
        },
      },
    ]);
    const state = EditorState.create({
      doc: value,
      extensions: [
        runKey,
        showLines ? [lineNumbers(), highlightActiveLineGutter()] : [],
        history(),
        drawSelection(),
        indentOnInput(),
        bracketMatching(),
        closeBrackets(),
        autocompletion({ activateOnTyping: true, icons: false }),
        highlightActiveLine(),
        highlightSelectionMatches(),
        syntaxHighlighting(highlight),
        keymap.of([...closeBracketsKeymap, ...defaultKeymap, ...searchKeymap, ...historyKeymap, ...completionKeymap, indentWithTab]),
        langComp.current.of(languageExt(language, schema)),
        roComp.current.of([EditorState.readOnly.of(readOnly), EditorView.editable.of(!readOnly)]),
        placeholder ? placeholderExt(placeholder) : [],
        EditorView.lineWrapping,
        baseTheme,
        EditorView.contentAttributes.of({ "aria-label": ariaLabel }),
        EditorView.updateListener.of((u) => {
          if (u.docChanged) onChangeRef.current?.(u.state.doc.toString());
        }),
      ],
    });
    const v = new EditorView({ state, parent: host.current });
    view.current = v;
    if (autoFocus) v.focus();
    return () => {
      v.destroy();
      view.current = null;
    };
    // Editor is created once; value/language/readOnly changes are applied below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  React.useEffect(() => {
    const v = view.current;
    if (!v) return;
    const current = v.state.doc.toString();
    if (current !== value) v.dispatch({ changes: { from: 0, to: current.length, insert: value } });
  }, [value]);

  React.useEffect(() => {
    view.current?.dispatch({ effects: langComp.current.reconfigure(languageExt(language, schema)) });
  }, [language, schema]);

  React.useEffect(() => {
    view.current?.dispatch({
      effects: roComp.current.reconfigure([EditorState.readOnly.of(readOnly), EditorView.editable.of(!readOnly)]),
    });
  }, [readOnly]);

  React.useImperativeHandle(ref, () => ({
    focus: () => view.current?.focus(),
    getSelection: () => {
      const v = view.current;
      if (!v) return "";
      const sel = v.state.selection.main;
      return sel.empty ? "" : v.state.sliceDoc(sel.from, sel.to);
    },
    insert: (text: string) => {
      const v = view.current;
      if (!v) return;
      const sel = v.state.selection.main;
      v.dispatch({ changes: { from: sel.from, to: sel.to, insert: text }, selection: { anchor: sel.from + text.length } });
      v.focus();
    },
  }));

  return (
    <div
      ref={host}
      className={cn("overflow-auto scrollbar-thin", className)}
      style={{ minHeight, maxHeight }}
      data-testid="code-editor"
    />
  );
});
