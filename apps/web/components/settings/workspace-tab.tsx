"use client";
import * as React from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { workspaces } from "@/lib/api/endpoints";
import { qk } from "@/lib/api/keys";
import type { WorkspaceSettingsDoc } from "@/lib/api/types";
import { useWorkspace } from "@/components/providers/workspace";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { ConfirmDialog } from "@/components/ui/alert-dialog";
import { ErrorState, InlineError, LoadingState } from "@/components/states/states";
import { DefinitionList } from "@/components/shell/page";
import { formatDateTime } from "@/lib/format";
import { RoleNotice, SettingsSection } from "./shared";

export const settingsKey = (ws: string) => ["ws", ws, "settings"] as const;

export function useWorkspaceSettings(ws: string) {
  return useQuery({ queryKey: settingsKey(ws), queryFn: () => workspaces.settings(ws) });
}

function InvestigationDefaults({ settings, disabled }: { settings: WorkspaceSettingsDoc; disabled: boolean }) {
  const { id: ws } = useWorkspace();
  const qc = useQueryClient();
  const [form, setForm] = React.useState(settings.investigation);
  React.useEffect(() => setForm(settings.investigation), [settings.investigation]);
  const save = useMutation({
    mutationFn: () => workspaces.updateSettings(ws, { investigation: form }),
    onSuccess: (s) => {
      qc.setQueryData(settingsKey(ws), s);
      toast.success("Investigation defaults saved");
    },
  });
  const dirty = JSON.stringify(form) !== JSON.stringify(settings.investigation);
  return (
    <form
      className="grid max-w-lg gap-3 sm:grid-cols-2"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <Field label="Drill depth" htmlFor="inv-depth" hint="How many levels the engine drills into top segments (0–4).">
        <Input
          id="inv-depth"
          type="number"
          min={0}
          max={4}
          disabled={disabled}
          value={form.max_depth}
          onChange={(e) => setForm({ ...form, max_depth: Number(e.target.value) })}
        />
      </Field>
      <Field label="Top segments per level" htmlFor="inv-top" hint="Segments drilled per dimension (1–10).">
        <Input
          id="inv-top"
          type="number"
          min={1}
          max={10}
          disabled={disabled}
          value={form.top_segments}
          onChange={(e) => setForm({ ...form, top_segments: Number(e.target.value) })}
        />
      </Field>
      <label className="flex items-center gap-2 text-sm sm:col-span-2">
        <Switch
          checked={form.require_plan_approval}
          disabled={disabled}
          onCheckedChange={(v) => setForm({ ...form, require_plan_approval: v })}
          aria-label="Require plan approval"
        />
        Show the analysis plan for approval before running expensive investigations
      </label>
      <InlineError error={save.error} className="sm:col-span-2" />
      {!disabled ? (
        <div className="sm:col-span-2">
          <Button type="submit" variant="primary" disabled={!dirty || save.isPending}>
            Save defaults
          </Button>
        </div>
      ) : null}
    </form>
  );
}

export function WorkspaceTab() {
  const { id: ws, workspace } = useWorkspace();
  const qc = useQueryClient();
  const router = useRouter();
  const isOwner = workspace?.role === "owner";
  const settings = useWorkspaceSettings(ws);
  const [name, setName] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [confirmDelete, setConfirmDelete] = React.useState(false);
  const [confirmText, setConfirmText] = React.useState("");

  React.useEffect(() => {
    if (workspace) {
      setName(workspace.name);
      setDescription(workspace.description ?? "");
    }
  }, [workspace]);

  const update = useMutation({
    mutationFn: () => workspaces.update(ws, { name: name.trim(), description }),
    onSuccess: (w) => {
      qc.setQueryData(qk.workspace(ws), w);
      void qc.invalidateQueries({ queryKey: qk.workspaces });
      toast.success("Workspace updated");
    },
  });
  const remove = useMutation({
    mutationFn: () => workspaces.remove(ws),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.workspaces });
      try {
        localStorage.removeItem("aos-last-workspace");
      } catch {
        /* ignore */
      }
      router.replace("/");
    },
    onError: (e: Error) => toast.error("Could not delete the workspace", { description: e.message }),
  });

  if (!workspace) return <LoadingState rows={4} />;
  const dirty = name.trim() !== workspace.name || description !== (workspace.description ?? "");

  return (
    <div>
      {!isOwner ? <RoleNotice /> : null}
      <SettingsSection title="General" description="Name and purpose, shown in the workspace switcher and on reports.">
        <form
          className="flex max-w-lg flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) update.mutate();
          }}
        >
          <Field label="Name" htmlFor="ws-name-edit">
            <Input id="ws-name-edit" value={name} disabled={!isOwner} onChange={(e) => setName(e.target.value)} required />
          </Field>
          <Field label="Description" htmlFor="ws-desc">
            <Textarea id="ws-desc" value={description} disabled={!isOwner} onChange={(e) => setDescription(e.target.value)} rows={3} />
          </Field>
          <InlineError error={update.error} />
          {isOwner ? (
            <div>
              <Button type="submit" variant="primary" disabled={!dirty || !name.trim() || update.isPending}>
                Save changes
              </Button>
            </div>
          ) : null}
        </form>
        <DefinitionList
          className="mt-4 max-w-lg text-xs"
          items={[
            { label: "Workspace ID", value: workspace.id, mono: true },
            { label: "Your role", value: workspace.role ?? "n/a" },
            { label: "Created", value: formatDateTime(workspace.created_at) },
          ]}
        />
      </SettingsSection>
      <SettingsSection
        title="Investigation defaults"
        description="How deep the deterministic engine drills and whether expensive plans wait for your approval."
      >
        {settings.isLoading ? (
          <LoadingState rows={2} />
        ) : settings.isError ? (
          <ErrorState compact error={settings.error} onRetry={() => void settings.refetch()} />
        ) : settings.data ? (
          <InvestigationDefaults settings={settings.data} disabled={!isOwner} />
        ) : null}
      </SettingsSection>
      {isOwner ? (
        <SettingsSection
          title="Danger zone"
          description="Deleting removes the workspace's analytical store, uploaded files and every object in it."
        >
          <div className="flex max-w-lg flex-col gap-2 rounded-md border border-negative/40 p-3">
            <Field label={`Type the workspace name (${workspace.name}) to confirm`} htmlFor="ws-del-confirm">
              <Input id="ws-del-confirm" value={confirmText} onChange={(e) => setConfirmText(e.target.value)} autoComplete="off" />
            </Field>
            <div>
              <Button variant="danger" disabled={confirmText !== workspace.name || remove.isPending} onClick={() => setConfirmDelete(true)}>
                Delete workspace
              </Button>
            </div>
          </div>
          <ConfirmDialog
            open={confirmDelete}
            onOpenChange={setConfirmDelete}
            title={`Delete “${workspace.name}”?`}
            description="This permanently deletes all datasets, metrics, investigations, findings, dashboards and reports in this workspace. It cannot be undone."
            confirmLabel="Delete permanently"
            destructive
            pending={remove.isPending}
            onConfirm={() => remove.mutate()}
          />
        </SettingsSection>
      ) : null}
    </div>
  );
}
