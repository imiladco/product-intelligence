"use client";

import { useCallback, useId, useState } from "react";
import { useRouter } from "next/navigation";
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
import { Skeleton } from "@/components/ui/skeleton";
import { apiFetch } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import type { DiscoveredResource, DiscoveredResources } from "@/lib/api/types";

type LoadState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "loaded"; resources: DiscoveredResource[]; truncated: boolean }
  | { kind: "failed"; message: string };

function errorMessage(error: unknown): string {
  return error instanceof ApiError
    ? error.message
    : "Could not reach the server. Please try again.";
}

/**
 * Group in render order, or don't.
 *
 * Grouping is decided by the data, never by which provider sent it: some
 * providers' resources have a parent to group under and some have none. When
 * nothing is grouped, the list renders flat rather than under an invented
 * heading. The backend has already sorted, so insertion order is render order.
 */
function intoGroups(
  resources: DiscoveredResource[],
): { grouped: boolean; groups: [string, DiscoveredResource[]][] } {
  const grouped = resources.some((resource) => resource.group_label !== "");
  if (!grouped) return { grouped: false, groups: [["", resources]] };

  const groups = new Map<string, DiscoveredResource[]>();
  for (const resource of resources) {
    const key = resource.group_label;
    groups.set(key, [...(groups.get(key) ?? []), resource]);
  }
  return { grouped: true, groups: [...groups.entries()] };
}

/**
 * Chooses which external resource an integration uses.
 *
 * Provider-neutral by construction: this component knows it is picking one
 * item from a list, and nothing else. `provider` goes straight into the
 * request path and is never inspected; `providerName` is interpolated into
 * copy and never compared. Neither appears in a conditional anywhere below,
 * which is what makes "no provider-specific behaviour" checkable rather than
 * merely intended.
 *
 * The browser sends back the identifier it was given and nothing else. The
 * resource's name is whatever the backend read from the provider when it
 * verified the choice — this component never submits a label, because a label
 * from here would not be evidence of anything.
 */
export function ResourcePickerDialog({
  projectId,
  provider,
  providerName,
  triggerLabel = "Choose property",
}: {
  projectId: number | string;
  provider: string;
  /** Human name of the provider, for copy only. Never compared or branched on. */
  providerName: string;
  triggerLabel?: string;
}) {
  const router = useRouter();
  const groupName = useId();
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<LoadState>({ kind: "idle" });
  const [selected, setSelected] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const data = await apiFetch<DiscoveredResources>(
        `/projects/${projectId}/integrations/${provider}/resources`,
      );
      setState({ kind: "loaded", resources: data.resources, truncated: data.truncated });
    } catch (error) {
      setState({ kind: "failed", message: errorMessage(error) });
    }
  }, [projectId, provider]);

  // Fetched when the dialog opens, not when the page renders: a list of
  // properties is a call to Google, and most page views never open this.
  function onOpenChange(next: boolean) {
    setOpen(next);
    if (next) {
      setSelected(null);
      void load();
    }
  }

  async function save() {
    if (!selected) return;
    setSaving(true);
    try {
      await apiFetch(`/projects/${projectId}/integrations/${provider}/resource`, {
        method: "POST",
        body: { resource_id: selected },
      });
      setOpen(false);
      // The server owns what the card shows next, so re-read it rather than
      // guessing the new state here.
      router.refresh();
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogTrigger asChild>
        <Button variant="default">{triggerLabel}</Button>
      </DialogTrigger>
      <DialogContent data-testid="resource-picker">
        <DialogHeader>
          <DialogTitle>Select a property</DialogTitle>
          <DialogDescription>
            These are the properties the connected Google account can use in
            {" "}
            {providerName}.
          </DialogDescription>
        </DialogHeader>

        {state.kind === "loading" ? (
          <div className="space-y-2" data-testid="resource-picker-loading">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : null}

        {state.kind === "failed" ? (
          <div className="space-y-3">
            <p role="alert" className="text-sm text-destructive">
              {state.message}
            </p>
            <Button variant="outline" onClick={() => void load()}>
              Try again
            </Button>
          </div>
        ) : null}

        {state.kind === "loaded" && state.resources.length === 0 ? (
          <div className="space-y-2 text-sm text-muted-foreground">
            <p>
              No properties are available to this Google account in{" "}
              {providerName}.
            </p>
            <p>
              Ask an administrator for access to a property, or connect a
              different Google account.
            </p>
          </div>
        ) : null}

        {state.kind === "loaded" && state.resources.length > 0 ? (
          <div className="max-h-80 space-y-4 overflow-y-auto">
            {state.truncated ? (
              <p className="text-xs text-muted-foreground">
                Showing the first properties found. This account has more than
                can be listed here.
              </p>
            ) : null}
            {intoGroups(state.resources).groups.map(([groupLabel, resources]) => (
              <fieldset key={groupLabel} className="space-y-1">
                {groupLabel ? (
                  <legend className="text-xs text-muted-foreground">
                    {groupLabel}
                  </legend>
                ) : null}
                {resources.map((resource) => (
                  <label
                    key={resource.id}
                    className="flex cursor-pointer items-start gap-3 rounded-md border p-3 text-sm hover:bg-accent"
                  >
                    <input
                      type="radio"
                      name={groupName}
                      value={resource.id}
                      checked={selected === resource.id}
                      onChange={() => setSelected(resource.id)}
                      className="mt-1"
                    />
                    <span className="space-y-0.5">
                      <span className="block font-medium">{resource.label}</span>
                      <span className="block text-xs text-muted-foreground">
                        {resource.resource_type || resource.id}
                      </span>
                    </span>
                  </label>
                ))}
              </fieldset>
            ))}
          </div>
        ) : null}

        <DialogFooter>
          <Button
            onClick={() => void save()}
            disabled={!selected || saving}
          >
            {saving ? "Saving…" : "Use this property"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
