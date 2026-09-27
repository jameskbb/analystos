"use client";
import * as React from "react";
import { Dialog, DialogBody, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Kbd } from "@/components/ui/kbd";
import { PRIMARY_NAV, SECONDARY_NAV } from "./nav";

export const SHORTCUTS: { keys: string[]; label: string; group: string }[] = [
  { keys: ["⌘", "K"], label: "Command palette", group: "General" },
  { keys: ["⌘", "/"], label: "Search the workspace", group: "General" },
  { keys: ["N"], label: "New investigation", group: "General" },
  { keys: ["?"], label: "Show keyboard shortcuts", group: "General" },
  { keys: ["⌘", "↵"], label: "Run query / cell", group: "Editors" },
  { keys: ["⇧", "↵"], label: "Run cell and advance (notebook)", group: "Editors" },
  { keys: ["⌘", "S"], label: "Save query", group: "Editors" },
  { keys: ["↑", "↓"], label: "Move between tree nodes", group: "Investigation" },
  { keys: ["←", "→"], label: "Collapse / expand node", group: "Investigation" },
  { keys: ["F"], label: "Save selected node as finding", group: "Investigation" },
  { keys: ["/"], label: "Focus the command bar", group: "Investigation" },
  ...[...PRIMARY_NAV, ...SECONDARY_NAV]
    .filter((n) => n.goKey)
    .map((n) => ({ keys: ["G", n.goKey!.toUpperCase()], label: `Go to ${n.label}`, group: "Navigation" })),
];

export function ShortcutsDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const groups = [...new Set(SHORTCUTS.map((s) => s.group))];
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg">
        <DialogHeader>
          <DialogTitle>Keyboard shortcuts</DialogTitle>
          <DialogDescription>⌘ is Ctrl on Windows and Linux. Single-key shortcuts work when you are not typing.</DialogDescription>
        </DialogHeader>
        <DialogBody className="grid gap-x-8 gap-y-4 sm:grid-cols-2">
          {groups.map((g) => (
            <div key={g}>
              <div className="mb-1.5 text-2xs font-semibold tracking-wider text-fg-subtle uppercase">{g}</div>
              <ul className="flex flex-col">
                {SHORTCUTS.filter((s) => s.group === g).map((s) => (
                  <li key={s.label} className="flex h-7 items-center justify-between gap-4 border-b border-border text-sm last:border-b-0">
                    <span>{s.label}</span>
                    <span className="flex gap-0.5">
                      {s.keys.map((k, i) => (
                        <Kbd key={i}>{k}</Kbd>
                      ))}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}
