"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

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
import { apiFetch } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";

const GOOGLE_PERMISSIONS_URL = "https://myaccount.google.com/permissions";

/**
 * Ends an integration, after saying exactly what that does.
 *
 * The copy is the point. Disconnecting deletes the stored credentials here and
 * deliberately does **not** revoke the grant in the user's Google account —
 * one consent can cover more than this integration, so revoking on their
 * behalf could break something they never asked us to touch. A user who wants
 * that too is sent to their Google account permissions, with a warning that it
 * applies to everything sharing the authorization. Being vague here would be
 * the security failure, not the UX one.
 *
 * Provider-neutral: `provider` goes into the request path and is never
 * inspected; `providerName` appears in copy and is never compared.
 */
export function DisconnectDialog({
  projectId,
  provider,
  providerName,
}: {
  projectId: number | string;
  provider: string;
  /** Human name of the provider, for copy only. Never compared or branched on. */
  providerName: string;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  async function confirm() {
    setPending(true);
    setFailure(null);
    try {
      await apiFetch(`/projects/${projectId}/integrations/${provider}/disconnect`, {
        method: "POST",
      });
      setOpen(false);
      // The server owns what the card shows next.
      router.refresh();
    } catch (error) {
      setFailure(
        error instanceof ApiError
          ? error.message
          : "Could not reach the server. Please try again.",
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline">Disconnect</Button>
      </DialogTrigger>
      <DialogContent data-testid="disconnect-dialog">
        <DialogHeader>
          <DialogTitle>Disconnect {providerName}?</DialogTitle>
          <DialogDescription>
            The stored access for this project is deleted. The selected
            property is remembered, so reconnecting restores it without picking
            again.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 text-sm text-muted-foreground">
          <p>
            This does not revoke access in your Google account — that
            authorization remains until you remove it yourself.
          </p>
          <p>
            You can remove it from your{" "}
            <a
              className="underline"
              href={GOOGLE_PERMISSIONS_URL}
              target="_blank"
              rel="noreferrer noopener"
            >
              Google account permissions
            </a>
            . Removing it there affects every integration sharing that
            authorization, in this project and any other.
          </p>
        </div>

        {failure ? (
          <p role="alert" className="text-sm text-destructive">
            {failure}
          </p>
        ) : null}

        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)} disabled={pending}>
            Cancel
          </Button>
          <Button onClick={() => void confirm()} disabled={pending}>
            {pending ? "Disconnecting…" : "Yes, disconnect"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
