import { Badge } from "@/components/ui/badge";
import type { IntegrationStatus } from "@/lib/api/types";
import { statusPresentation } from "@/lib/integrations/status";

export function StatusBadge({ status }: { status: IntegrationStatus }) {
  const { label, variant } = statusPresentation(status);
  return (
    <Badge variant={variant} data-testid="status-badge" data-status={status}>
      {label}
    </Badge>
  );
}
