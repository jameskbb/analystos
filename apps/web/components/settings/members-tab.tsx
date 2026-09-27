"use client";
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { UserPlus, Users, X } from "lucide-react";
import { workspaces } from "@/lib/api/endpoints";
import { qk } from "@/lib/api/keys";
import type { Member, Role } from "@/lib/api/types";
import { useWorkspace } from "@/components/providers/workspace";
import { useSession } from "@/components/providers/session";
import { Button } from "@/components/ui/button";
import { Input, NativeSelect } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { ConfirmDialog } from "@/components/ui/alert-dialog";
import { EmptyState, InlineError, QueryState } from "@/components/states/states";
import { formatDate } from "@/lib/format";
import { RoleNotice, SettingsSection } from "./shared";
import { InvitesSection } from "./invites";

export const ROLE_HELP: Record<Role, string> = {
  owner: "Everything, plus members, settings, credentials and deleting the workspace",
  editor: "Create and change data, metrics, investigations, findings, dashboards and reports",
  viewer: "Read, run read-only queries and analyses, and export",
};

export function MembersTab() {
  const { id: ws, workspace } = useWorkspace();
  const { data: session } = useSession();
  const qc = useQueryClient();
  const isOwner = workspace?.role === "owner";
  const members = useQuery({ queryKey: qk.members(ws), queryFn: () => workspaces.members(ws) });
  const [email, setEmail] = React.useState("");
  const [role, setRole] = React.useState<Role>("viewer");
  const [removing, setRemoving] = React.useState<Member | null>(null);

  const invalidate = () => qc.invalidateQueries({ queryKey: qk.members(ws) });
  const add = useMutation({
    mutationFn: () => workspaces.addMember(ws, { email: email.trim(), role }),
    onSuccess: (m) => {
      setEmail("");
      void invalidate();
      toast.success(`${m.name || m.email} added as ${m.role}`);
    },
  });
  const change = useMutation({
    mutationFn: ({ userId, r }: { userId: string; r: Role }) => workspaces.updateMember(ws, userId, r),
    onSuccess: () => void invalidate(),
    onError: (e: Error) => toast.error("Role not changed", { description: e.message }),
  });
  const remove = useMutation({
    mutationFn: (userId: string) => workspaces.removeMember(ws, userId),
    onSuccess: () => {
      setRemoving(null);
      void invalidate();
    },
    onError: (e: Error) => toast.error("Member not removed", { description: e.message }),
  });

  return (
    <div>
      {!isOwner ? <RoleNotice /> : null}
      {isOwner ? <InvitesSection /> : null}
      {isOwner ? (
        <SettingsSection
          title="Add an existing account"
          description="For people who already have an AnalystOS account on this server. Roles apply to this workspace only."
        >
          <form
            className="flex max-w-xl flex-wrap items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (email.trim()) add.mutate();
            }}
          >
            <Field label="Email" htmlFor="member-email" className="min-w-56 flex-1">
              <Input id="member-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="analyst@company.com" required />
            </Field>
            <Field label="Role" htmlFor="member-role" className="w-32">
              <NativeSelect id="member-role" value={role} onChange={(e) => setRole(e.target.value as Role)}>
                <option value="viewer">Viewer</option>
                <option value="editor">Editor</option>
                <option value="owner">Owner</option>
              </NativeSelect>
            </Field>
            <Button type="submit" variant="primary" disabled={!email.trim() || add.isPending}>
              <UserPlus /> Add
            </Button>
          </form>
          <p className="mt-1.5 text-xs text-fg-subtle">{ROLE_HELP[role]}.</p>
          <InlineError error={add.error} className="mt-2 max-w-xl" />
        </SettingsSection>
      ) : null}
      <SettingsSection title="Members" description="The last owner cannot be demoted or removed.">
        <QueryState
          query={members}
          empty={<EmptyState compact icon={Users} title="No members" />}
        >
          {(list) => (
            <div className="overflow-x-auto rounded-md border border-border">
              <table className="w-full text-sm">
                <caption className="sr-only">Workspace members</caption>
                <thead>
                  <tr className="border-b border-border bg-bg-subtle text-left text-2xs tracking-wide text-fg-subtle uppercase">
                    <th scope="col" className="px-3 py-1.5 font-medium">Member</th>
                    <th scope="col" className="px-3 py-1.5 font-medium">Role</th>
                    <th scope="col" className="px-3 py-1.5 font-medium">Added</th>
                    <th scope="col" className="w-10 px-3 py-1.5"><span className="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((m) => {
                    const me = m.user_id === session?.user?.id;
                    return (
                      <tr key={m.user_id} className="border-b border-border last:border-b-0">
                        <td className="px-3 py-1.5">
                          <div className="font-medium">
                            {m.name} {me ? <Badge tone="outline">You</Badge> : null}
                          </div>
                          <div className="text-xs text-fg-subtle">{m.email}</div>
                        </td>
                        <td className="px-3 py-1.5">
                          {isOwner ? (
                            <NativeSelect
                              aria-label={`Role for ${m.name}`}
                              className="w-28"
                              value={m.role}
                              disabled={change.isPending}
                              onChange={(e) => change.mutate({ userId: m.user_id, r: e.target.value as Role })}
                            >
                              <option value="viewer">Viewer</option>
                              <option value="editor">Editor</option>
                              <option value="owner">Owner</option>
                            </NativeSelect>
                          ) : (
                            <Badge tone={m.role === "owner" ? "accent" : "neutral"}>{m.role}</Badge>
                          )}
                        </td>
                        <td className="px-3 py-1.5 whitespace-nowrap text-fg-muted">{formatDate(m.created_at)}</td>
                        <td className="px-3 py-1.5 text-right">
                          {isOwner ? (
                            <Button variant="ghost" size="icon-xs" aria-label={`Remove ${m.name}`} onClick={() => setRemoving(m)}>
                              <X />
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
        <dl className="mt-3 grid max-w-xl gap-1 text-xs">
          {(Object.keys(ROLE_HELP) as Role[]).map((r) => (
            <div key={r} className="flex gap-2">
              <dt className="w-14 shrink-0 font-medium capitalize">{r}</dt>
              <dd className="text-fg-subtle">{ROLE_HELP[r]}</dd>
            </div>
          ))}
        </dl>
      </SettingsSection>
      <ConfirmDialog
        open={!!removing}
        onOpenChange={(o) => !o && setRemoving(null)}
        title={`Remove ${removing?.name ?? "member"}?`}
        description="They lose access to this workspace immediately. Their findings, comments and investigations stay."
        confirmLabel="Remove"
        destructive
        pending={remove.isPending}
        onConfirm={() => removing && remove.mutate(removing.user_id)}
      />
    </div>
  );
}
