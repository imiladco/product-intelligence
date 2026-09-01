"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiFetch } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import type { Project, Workspace } from "@/lib/api/types";

export function NewProjectDialog({ workspaces }: { workspaces: Workspace[] }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [websiteUrl, setWebsiteUrl] = useState("");
  const [workspaceId, setWorkspaceId] = useState<number | undefined>(workspaces[0]?.id);
  const [pending, setPending] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);

  function reset() {
    setName("");
    setWebsiteUrl("");
    setErrors({});
    setFormError(null);
  }

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setErrors({});
    setFormError(null);

    try {
      const project = await apiFetch<Project>("/projects", {
        method: "POST",
        body: { name, website_url: websiteUrl, workspace: workspaceId },
      });
      setOpen(false);
      reset();
      toast.success(`Created ${project.name}`);
      router.refresh();
    } catch (error) {
      if (error instanceof ApiError) {
        const fields: Record<string, string> = {};
        for (const [field, messages] of Object.entries(error.fieldErrors)) {
          if (messages[0]) fields[field] = messages[0];
        }
        setErrors(fields);
        if (!Object.keys(fields).length) setFormError(error.message);
      } else {
        setFormError("Could not reach the server. Please try again.");
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button>New project</Button>
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={onSubmit} noValidate>
          <DialogHeader>
            <DialogTitle>New project</DialogTitle>
            <DialogDescription>
              Name the product and enter the website it represents.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-6">
            {formError ? (
              <p role="alert" className="text-sm text-destructive">
                {formError}
              </p>
            ) : null}

            <div className="space-y-2">
              <Label htmlFor="project-name">Name</Label>
              <Input
                id="project-name"
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                aria-invalid={Boolean(errors.name)}
              />
              {errors.name ? <p className="text-sm text-destructive">{errors.name}</p> : null}
            </div>

            <div className="space-y-2">
              <Label htmlFor="project-website">Website</Label>
              <Input
                id="project-website"
                required
                placeholder="example.com"
                value={websiteUrl}
                onChange={(e) => setWebsiteUrl(e.target.value)}
                aria-invalid={Boolean(errors.website_url)}
              />
              {errors.website_url ? (
                <p className="text-sm text-destructive">{errors.website_url}</p>
              ) : null}
            </div>

            {workspaces.length > 1 ? (
              <div className="space-y-2">
                <Label htmlFor="project-workspace">Workspace</Label>
                <select
                  id="project-workspace"
                  className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-xs"
                  value={workspaceId}
                  onChange={(e) => setWorkspaceId(Number(e.target.value))}
                >
                  {workspaces.map((workspace) => (
                    <option key={workspace.id} value={workspace.id}>
                      {workspace.name}
                    </option>
                  ))}
                </select>
                {errors.workspace ? (
                  <p className="text-sm text-destructive">{errors.workspace}</p>
                ) : null}
              </div>
            ) : null}
          </div>

          <DialogFooter>
            <Button type="submit" disabled={pending}>
              {pending ? "Creating…" : "Create project"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
