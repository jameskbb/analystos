"use client";
import * as React from "react";
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { Field } from "@/components/ui/label";
import { InlineError } from "@/components/states/states";

export interface SaveQueryValues {
  name: string;
  description: string;
  tags: string[];
  asNew: boolean;
}

/** Save or update a query. When editing an existing saved query the user can also "save as new". */
export function SaveQueryDialog({
  open,
  onOpenChange,
  initial,
  existing,
  pending,
  error,
  onSave,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  initial: { name: string; description: string; tags: string[] };
  existing: boolean;
  pending: boolean;
  error: unknown;
  onSave: (v: SaveQueryValues) => void;
}) {
  const [name, setName] = React.useState(initial.name);
  const [description, setDescription] = React.useState(initial.description);
  const [tags, setTags] = React.useState(initial.tags.join(", "));
  React.useEffect(() => {
    if (open) {
      setName(initial.name);
      setDescription(initial.description);
      setTags(initial.tags.join(", "));
    }
  }, [open, initial.name, initial.description, initial.tags]);
  const values = (asNew: boolean): SaveQueryValues => ({
    name: name.trim(),
    description: description.trim(),
    tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
    asNew,
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="sm">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) onSave(values(false));
          }}
        >
          <DialogHeader>
            <DialogTitle>{existing ? "Update saved query" : "Save query"}</DialogTitle>
            <DialogDescription>Parameters and their types are saved with the query.</DialogDescription>
          </DialogHeader>
          <DialogBody className="flex flex-col gap-3">
            <Field label="Name" htmlFor="sq-name">
              <Input id="sq-name" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Revenue by branch, last 90 days" />
            </Field>
            <Field label="Description" htmlFor="sq-desc">
              <Textarea id="sq-desc" value={description} onChange={(e) => setDescription(e.target.value)} rows={3} />
            </Field>
            <Field label="Tags" htmlFor="sq-tags" hint="Comma separated">
              <Input id="sq-tags" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="finance, weekly" />
            </Field>
            <InlineError error={error} />
          </DialogBody>
          <DialogFooter>
            {existing ? (
              <Button variant="secondary" disabled={!name.trim() || pending} onClick={() => onSave(values(true))}>
                Save as new
              </Button>
            ) : (
              <Button variant="secondary" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
            )}
            <Button variant="primary" type="submit" disabled={!name.trim() || pending}>
              {existing ? "Update" : "Save"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
