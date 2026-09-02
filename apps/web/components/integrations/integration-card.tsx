import { ConnectButton } from "@/components/integrations/connect-button";
import { StatusBadge } from "@/components/integrations/status-badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import type { IntegrationEntry } from "@/lib/api/types";

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

        <div className="flex items-center gap-3">
          <ConnectButton
            projectId={projectId}
            provider={entry.provider}
            label={connection ? "Reconnect" : "Connect"}
            variant={connection ? "outline" : "default"}
          />
          {entry.status === "awaiting_resource_selection" ? (
            // Choosing the property arrives with resource discovery. Until
            // then the card says so rather than offering an action that would
            // do nothing.
            <span className="text-xs text-muted-foreground">
              Choosing a property is not available yet.
            </span>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
