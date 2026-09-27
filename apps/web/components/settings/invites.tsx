"use client";
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Check, Copy, Mail, X } from "lucide-react";
import { invites, type Invite, type InviteCreated } from "@/lib/api/endpoints";
import type { Role } from "@/lib/api/types";
import { useWorkspace } from "@/components/providers/workspace";
import { Button } from "@/components/ui/button";
import { Input, NativeSelect } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { EmptyState, InlineError, QueryState } from "@/components/states/states";
import { formatDate } from "@/lib/format";
import { SettingsSection } from "./shared";

/** Absolute invite link for the API's `accept_path` (`/invite/{token}`) on this web origin. */
export function inviteLink(acceptPath: string, origin: string): string {
  return `${origin.replace(/\/$/, "")}${acceptPath.startsWith("/") ? acceptPath : `/${acceptPath}`}`;
}

const STATUS_TONE: Record<Invite["status"], "accent" | "positive" | "neutral" | "warning"> = {
  pending: "accent",
  accepted: "positive",
  revoked: "neutral",
  expired: "warning",
};

/** Shows a new invite link exactly once (the server stores only a hash of the token). */
export function InviteReveal({ created, onDone }: { created: InviteCreated; onDone: () => void }) {
  const link = inviteLink(created.accept_path, typeof window === "undefined" ? "" : window.location.origin);
  const [copied, setCopied] = React.useState(false);
  const [copyError, setCopyError] = React.useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      setCopyError(false);
    } catch {
      setCopyError(true);
    }
  };
  return (
    <div role="alert" className="mt-3 max-w-xl rounded-md border border-warning/40 bg-warning-soft p-3">
      <div className="text-sm font-semibold text-warning">
        Send this link to {created.invite.email} now
      </div>
      <p className="mt-0.5 text-xs text-fg-muted">
        It is shown only once, works once and expires {formatDate(created.invite.expires_at)}. The invitee chooses their
        own password. Revoke the invite and create a new one if the link is lost.
      </p>
      <div className="mt-2 flex items-center gap-2">
        <code data-testid="invite-link" className="min-w-0 flex-1 truncate rounded border border-border-strong bg-bg px-2 py-1 font-mono text-xs select-all">
          {link}
        </code>
        <Button size="sm" onClick={() => void copy()} aria-label="Copy invite link">
          {copied ? <Check className="text-positive" /> : <Copy />}
          {copied ? "Copied" : "Copy"}
        </Button>
        <Button size="sm" variant="ghost" onClick={onDone}>
          Done
        </Button>
      </div>
      {copyError ? <p className="mt-1 text-xs text-negative">Copy failed; select the link and copy it by hand.</p> : null}
    </div>
  );
}

/** Owner-only: invite by email with a role, copy the one-time link, list and revoke invites. */
export function InvitesSection() {
  const { id: ws } = useWorkspace();
  const qc = useQueryClient();
  const key = ["ws", ws, "invites"] as const;
  const list = useQuery({ queryKey: key, queryFn: () => invites.list(ws) });
  const [email, setEmail] = React.useState("");
  const [role, setRole] = React.useState<Role>("viewer");
  const [created, setCreated] = React.useState<InviteCreated | null>(null);
  const create = useMutation({
    mutationFn: () => invites.create(ws, { email: email.trim(), role }),
    onSuccess: (res) => {
      setCreated(res);
      setEmail("");
      void qc.invalidateQueries({ queryKey: key });
    },
  });
  const revoke = useMutation({
    mutationFn: (id: string) => invites.revoke(ws, id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: key });
      toast.success("Invite revoked; its link no longer works");
    },
    onError: (e: Error) => toast.error("Invite not revoked", { description: e.message }),
  });

  return (
    <SettingsSection
      title="Invite people"
      description="New people join through a single-use invite link and set their own password. Existing accounts sign in and accept."
    >
      <form
        className="flex max-w-xl flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (email.trim()) create.mutate();
        }}
      >
        <Field label="Email" htmlFor="invite-email" className="min-w-56 flex-1">
          <Input id="invite-email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="colleague@company.com" />
        </Field>
        <Field label="Role" htmlFor="invite-role" className="w-32">
          <NativeSelect id="invite-role" value={role} onChange={(e) => setRole(e.target.value as Role)}>
            <option value="viewer">Viewer</option>
            <option value="editor">Editor</option>
            <option value="owner">Owner</option>
          </NativeSelect>
        </Field>
        <Button type="submit" variant="primary" disabled={!email.trim() || create.isPending}>
          <Mail /> Create invite
        </Button>
      </form>
      <InlineError error={create.error} className="mt-2 max-w-xl" />
      {created ? <InviteReveal created={created} onDone={() => setCreated(null)} /> : null}
      <div className="mt-4">
        <QueryState query={list} empty={<EmptyState compact icon={Mail} title="No invites yet" />}>
          {(items) => (
            <div className="overflow-x-auto rounded-md border border-border">
              <table className="w-full text-sm" aria-label="Invites">
                <thead>
                  <tr className="border-b border-border bg-bg-subtle text-left text-2xs tracking-wide text-fg-subtle uppercase">
                    <th scope="col" className="px-3 py-1.5 font-medium">Email</th>
                    <th scope="col" className="px-3 py-1.5 font-medium">Role</th>
                    <th scope="col" className="px-3 py-1.5 font-medium">Status</th>
                    <th scope="col" className="px-3 py-1.5 font-medium">Expires</th>
                    <th scope="col" className="w-10 px-3 py-1.5"><span className="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((inv) => (
                    <tr key={inv.id} className="border-b border-border last:border-b-0">
                      <td className="px-3 py-1.5">{inv.email}</td>
                      <td className="px-3 py-1.5 capitalize">{inv.role}</td>
                      <td className="px-3 py-1.5">
                        <Badge tone={STATUS_TONE[inv.status]}>{inv.status}</Badge>
                      </td>
                      <td className="px-3 py-1.5 whitespace-nowrap text-fg-muted">{formatDate(inv.expires_at)}</td>
                      <td className="px-3 py-1.5 text-right">
                        {inv.status === "pending" ? (
                          <Button
                            variant="ghost"
                            size="icon-xs"
                            aria-label={`Revoke invite for ${inv.email}`}
                            disabled={revoke.isPending}
                            onClick={() => revoke.mutate(inv.id)}
                          >
                            <X />
                          </Button>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </QueryState>
      </div>
    </SettingsSection>
  );
}
