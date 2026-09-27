"use client";
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Plus } from "lucide-react";
import { auth } from "@/lib/api/endpoints";
import { qk } from "@/lib/api/keys";
import type { ApiToken, ApiTokenCreated } from "@/lib/api/types";
import { useWorkspace } from "@/components/providers/workspace";
import { Button } from "@/components/ui/button";
import { Input, NativeSelect } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { ConfirmDialog } from "@/components/ui/alert-dialog";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { EmptyState, InlineError, QueryState } from "@/components/states/states";
import { formatDate, formatRelative } from "@/lib/format";
import { SettingsSection } from "./shared";
import { TokenReveal } from "./token-reveal";

export function tokenState(t: ApiToken, now = Date.now()): "active" | "revoked" | "expired" {
  if (t.revoked_at) return "revoked";
  if (t.expires_at && Date.parse(t.expires_at) < now) return "expired";
  return "active";
}

function CreateTokenDialog({ open, onOpenChange, onCreated }: { open: boolean; onOpenChange: (o: boolean) => void; onCreated: (t: ApiTokenCreated) => void }) {
  const { id: ws, workspace } = useWorkspace();
  const [name, setName] = React.useState("");
  const [scope, setScope] = React.useState<"workspace" | "all">("workspace");
  const [readOnly, setReadOnly] = React.useState(true);
  const [expiry, setExpiry] = React.useState("90");
  const create = useMutation({
    mutationFn: () =>
      auth.createToken({
        name: name.trim(),
        workspace_id: scope === "workspace" ? ws : null,
        read_only: readOnly,
        expires_in_days: expiry === "never" ? null : Number(expiry),
      }),
    onSuccess: (t) => {
      onCreated(t);
      onOpenChange(false);
      setName("");
    },
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="sm">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) create.mutate();
          }}
        >
          <DialogHeader>
            <DialogTitle>Create API token</DialogTitle>
            <DialogDescription>For the MCP server, scripts and automation. Tokens act as you, limited by the scope below.</DialogDescription>
          </DialogHeader>
          <DialogBody className="flex flex-col gap-3">
            <Field label="Name" htmlFor="tok-name" hint="What will use this token, e.g. “Claude Desktop MCP”.">
              <Input id="tok-name" autoFocus value={name} onChange={(e) => setName(e.target.value)} required />
            </Field>
            <Field label="Scope" htmlFor="tok-scope">
              <NativeSelect id="tok-scope" value={scope} onChange={(e) => setScope(e.target.value as "workspace" | "all")}>
                <option value="workspace">This workspace only ({workspace?.name ?? "current"})</option>
                <option value="all">All my workspaces</option>
              </NativeSelect>
            </Field>
            <Field label="Expires" htmlFor="tok-exp">
              <NativeSelect id="tok-exp" value={expiry} onChange={(e) => setExpiry(e.target.value)}>
                <option value="30">In 30 days</option>
                <option value="90">In 90 days</option>
                <option value="365">In 1 year</option>
                <option value="never">Never</option>
              </NativeSelect>
            </Field>
            <label className="flex items-start gap-2 text-sm">
              <Switch checked={readOnly} onCheckedChange={setReadOnly} aria-label="Read-only token" className="mt-0.5" />
              <span>
                Read-only
                <span className="block text-xs text-fg-subtle">
                  Can read and run read-only queries and analyses, but cannot create or change anything.
                </span>
              </span>
            </label>
            <InlineError error={create.error} />
          </DialogBody>
          <DialogFooter>
            <Button onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" variant="primary" disabled={!name.trim() || create.isPending}>
              Create token
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function TokensTab() {
  const qc = useQueryClient();
  const tokens = useQuery({ queryKey: qk.tokens, queryFn: auth.tokens });
  const [creating, setCreating] = React.useState(false);
  const [created, setCreated] = React.useState<ApiTokenCreated | null>(null);
  const [revoking, setRevoking] = React.useState<ApiToken | null>(null);
  const revoke = useMutation({
    mutationFn: (id: string) => auth.revokeToken(id),
    onSuccess: () => {
      setRevoking(null);
      void qc.invalidateQueries({ queryKey: qk.tokens });
    },
  });

  return (
    <div>
      <SettingsSection
        title="API tokens"
        description="Personal tokens for the REST API and the MCP server. Sent as “Authorization: Bearer aos_…”. Stored hashed; shown once."
        actions={
          <Button variant="primary" onClick={() => setCreating(true)}>
            <Plus /> New token
          </Button>
        }
      >
        {created ? (
          <div className="mb-3">
            <TokenReveal
              token={created.token}
              name={created.meta.name}
              onDone={() => {
                setCreated(null);
                void qc.invalidateQueries({ queryKey: qk.tokens });
              }}
            />
          </div>
        ) : null}
        <QueryState
          query={tokens}
          empty={
            <EmptyState
              compact
              icon={KeyRound}
              title="No API tokens"
              description="Create one to connect the AnalystOS MCP server or automate exports."
              action={
                <Button size="xs" onClick={() => setCreating(true)}>
                  New token
                </Button>
              }
            />
          }
        >
          {(list) => (
            <div className="overflow-x-auto rounded-md border border-border">
              <table className="w-full text-sm">
                <caption className="sr-only">API tokens</caption>
                <thead>
                  <tr className="border-b border-border bg-bg-subtle text-left text-2xs tracking-wide text-fg-subtle uppercase">
                    <th scope="col" className="px-3 py-1.5 font-medium">Name</th>
                    <th scope="col" className="px-3 py-1.5 font-medium">Token</th>
                    <th scope="col" className="px-3 py-1.5 font-medium">Access</th>
                    <th scope="col" className="px-3 py-1.5 font-medium">Last used</th>
                    <th scope="col" className="px-3 py-1.5 font-medium">Expires</th>
                    <th scope="col" className="px-3 py-1.5"><span className="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((t) => {
                    const state = tokenState(t);
                    return (
                      <tr key={t.id} className="border-b border-border last:border-b-0">
                        <td className="px-3 py-1.5">
                          <div className="font-medium">{t.name}</div>
                          <div className="text-2xs text-fg-subtle">Created {formatDate(t.created_at)}</div>
                        </td>
                        <td className="px-3 py-1.5 font-mono text-xs">{t.prefix}…</td>
                        <td className="px-3 py-1.5">
                          <div className="flex flex-wrap gap-1">
                            <Badge tone={t.read_only ? "neutral" : "accent"}>{t.read_only ? "Read-only" : "Read & write"}</Badge>
                            <Badge tone="outline">{t.workspace_id ? "One workspace" : "All workspaces"}</Badge>
                          </div>
                        </td>
                        <td className="px-3 py-1.5 whitespace-nowrap text-fg-muted">{t.last_used_at ? formatRelative(t.last_used_at) : "Never"}</td>
                        <td className="px-3 py-1.5 whitespace-nowrap">
                          {state === "active" ? (
                            <span className="text-fg-muted">{t.expires_at ? formatDate(t.expires_at) : "Never"}</span>
                          ) : (
                            <Badge tone="negative">{state === "revoked" ? "Revoked" : "Expired"}</Badge>
                          )}
                        </td>
                        <td className="px-3 py-1.5 text-right">
                          {state === "active" ? (
                            <Button variant="danger-ghost" size="xs" onClick={() => setRevoking(t)}>
                              Revoke
                            </Button>
                          ) : null}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </QueryState>
      </SettingsSection>
      <CreateTokenDialog open={creating} onOpenChange={setCreating} onCreated={setCreated} />
      <ConfirmDialog
        open={!!revoking}
        onOpenChange={(o) => !o && setRevoking(null)}
        title={`Revoke “${revoking?.name ?? ""}”?`}
        description="Anything using this token stops working immediately."
        confirmLabel="Revoke"
        destructive
        pending={revoke.isPending}
        onConfirm={() => revoking && revoke.mutate(revoking.id)}
      />
    </div>
  );
}
