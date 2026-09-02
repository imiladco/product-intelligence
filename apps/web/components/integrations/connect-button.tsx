"use client";

import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import type { AuthorizationStart } from "@/lib/api/types";

/**
 * Starts a Google authorization.
 *
 * The frontend holds no client id, no client secret, and no token. It asks
 * Django for the consent URL and follows it; Django owns the whole OAuth
 * exchange and the browser never sees credential material.
 */
export function ConnectButton({
  projectId,
  provider,
  label = "Connect",
  variant = "default",
}: {
  projectId: number | string;
  provider: string;
  label?: string;
  variant?: "default" | "outline";
}) {
  const [pending, setPending] = useState(false);

  async function connect() {
    setPending(true);
    try {
      const { authorization_url } = await apiFetch<AuthorizationStart>(
        `/projects/${projectId}/integrations/${provider}/authorize`,
      );
      // A full navigation, not a fetch: the user has to see Google's consent
      // screen on Google's own origin.
      window.location.assign(authorization_url);
    } catch (error) {
      const message =
        error instanceof ApiError
          ? error.message
          : "Could not reach the server. Please try again.";
      toast.error(message);
      setPending(false);
    }
  }

  return (
    <Button onClick={connect} disabled={pending} variant={variant}>
      {pending ? "Redirecting…" : label}
    </Button>
  );
}
