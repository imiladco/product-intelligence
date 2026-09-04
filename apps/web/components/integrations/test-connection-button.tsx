"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import type { IntegrationEntry } from "@/lib/api/types";

/**
 * Checks a connection against the provider, now, and says what came back.
 *
 * The outcome is read from the entry the server returns rather than from the
 * fact that the request succeeded. A check can complete and still report that
 * the property is gone or the credential is dead — announcing success on a 200
 * would tell the user the opposite of what was just recorded.
 *
 * Provider-neutral: `provider` goes into the request path and is never
 * inspected, so there is no branch here for any provider to appear in.
 */
export function TestConnectionButton({
  projectId,
  provider,
}: {
  projectId: number | string;
  provider: string;
}) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [outcome, setOutcome] = useState<string | null>(null);

  async function check() {
    setPending(true);
    setOutcome(null);
    try {
      const entry = await apiFetch<IntegrationEntry>(
        `/projects/${projectId}/integrations/${provider}/health-check`,
        { method: "POST" },
      );
      setOutcome(
        entry.connection?.last_error_message ||
          (entry.status === "connected" ? "Connected." : "Checked."),
      );
      // The server owns what the card shows next, so re-read it rather than
      // patching local state from a payload that may already be behind.
      router.refresh();
    } catch (error) {
      // A refused check says nothing about the connection itself, so the
      // message is the server's own and nothing is claimed on top of it.
      setOutcome(
        error instanceof ApiError
          ? error.message
          : "Could not reach the server. Please try again.",
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex items-center gap-2">
      <Button variant="outline" onClick={check} disabled={pending}>
        {pending ? "Checking…" : "Test connection"}
      </Button>
      {outcome ? (
        <span role="status" className="text-xs text-muted-foreground">
          {outcome}
        </span>
      ) : null}
    </div>
  );
}
