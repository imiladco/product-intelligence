import { ConnectButton } from "@/components/integrations/connect-button";
import { DisconnectDialog } from "@/components/integrations/disconnect-dialog";
import { ResourcePickerDialog } from "@/components/integrations/resource-picker-dialog";
import { StatusBadge } from "@/components/integrations/status-badge";
import { TestConnectionButton } from "@/components/integrations/test-connection-button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import type { IntegrationEntry } from "@/lib/api/types";
import { presentationFor } from "@/lib/integrations/status";

function formatTimestamp(value: string | null): string {
  if (!value) return "Never";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unknown";
  return date.toLocaleString();
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="space-y-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="truncate text-sm">{value}</dd>
    </div>
  );
}

export function IntegrationCard({
  entry,
  projectId,
}: {
  entry: IntegrationEntry;
  projectId: number | string;
}) {
  const { connection } = entry;
  // Status and the recorded error code together: `error` is reached by causes
  // whose repairs have nothing to do with each other, so the action offered is
  // keyed on both. The card reads neither itself — it asks the recovery model.
  const { actionLabel, note, ...presentation } = presentationFor(
    entry.status,
    connection?.last_error_code ?? "",
  );
  // Two independent questions, and the action needs a yes to both: does this
  // state call for choosing a resource, and can this provider offer any? The
  // status mapping answers only the first — it knows nothing about providers —
  // so gating on it alone renders a picker that 404s for a provider without a
  // catalog. Read from the entry, so no provider is named here.
  const resourceAction =
    entry.supports_resource_selection ? presentation.resourceAction : null;
  // Checking a connection means asking the provider about the resource it
  // points at, so with nothing selected there is nothing to check. Both gates
  // come from the entry itself; neither names a provider.
  const canTestConnection =
    presentation.canTestConnection && Boolean(connection?.external_resource_id);

  return (
    <Card data-testid={`integration-card-${entry.provider}`}>
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div className="space-y-1">
            <h2 className="font-medium leading-none">{entry.display_name}</h2>
            <p className="text-sm text-muted-foreground">{entry.description}</p>
          </div>
          <StatusBadge status={entry.status} />
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {connection ? (
          <dl className="grid gap-3 sm:grid-cols-3">
            <DetailRow
              label="Selected property"
              value={connection.external_resource_label || "None selected"}
            />
            <DetailRow
              label="Last successful access"
              value={formatTimestamp(connection.last_successful_check_at)}
            />
            <DetailRow
              label="Last checked"
              value={formatTimestamp(connection.last_health_check_at)}
            />
          </dl>
        ) : null}

        {connection?.last_error_message ? (
          <p role="alert" className="text-sm text-destructive">
            {connection.last_error_message}
          </p>
        ) : null}

        {/* Which action a state offers comes from the status mapping, so the
            card never invents one. A state that already has what it needs from
            Google offers no authorization action at all. */}
        {actionLabel ||
        resourceAction ||
        canTestConnection ||
        presentation.canDisconnect ||
        note ? (
          <div className="flex items-center gap-3">
            {actionLabel ? (
              <ConnectButton
                projectId={projectId}
                provider={entry.provider}
                label={actionLabel}
                variant={entry.status === "not_connected" ? "default" : "outline"}
              />
            ) : null}
            {canTestConnection ? (
              <TestConnectionButton
                projectId={projectId}
                provider={entry.provider}
              />
            ) : null}
            {resourceAction ? (
              <ResourcePickerDialog
                projectId={projectId}
                provider={entry.provider}
                providerName={entry.display_name}
                // The same dialog, and the same request: only the word on the
                // trigger differs, because choosing a first property and
                // replacing one are different things to the user and one
                // operation to the backend.
                triggerLabel={
                  resourceAction === "change" ? "Change property" : "Choose property"
                }
              />
            ) : null}
            {presentation.canDisconnect ? (
              <DisconnectDialog
                projectId={projectId}
                provider={entry.provider}
                providerName={entry.display_name}
              />
            ) : null}
            {note ? (
              <span className="text-xs text-muted-foreground">{note}</span>
            ) : null}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
