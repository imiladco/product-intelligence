import type { IntegrationStatus } from "@/lib/api/types";

type BadgeVariant = "default" | "secondary" | "destructive" | "outline";

interface StatusPresentation {
  /** Concise user-facing label. */
  label: string;
  variant: BadgeVariant;
  /** True when the state needs the user to do something. */
  needsAttention: boolean;
}

/**
 * The single mapping from status to how it reads in the UI.
 *
 * Centralized on purpose: components ask this module, and never compare status
 * strings themselves, so adding or renaming a state is one edit.
 */
const PRESENTATION: Record<IntegrationStatus, StatusPresentation> = {
  not_connected: { label: "Not connected", variant: "outline", needsAttention: false },
  pending_authorization: { label: "Connecting", variant: "secondary", needsAttention: false },
  awaiting_resource_selection: {
    label: "Select a property",
    variant: "secondary",
    needsAttention: true,
  },
  connected: { label: "Connected", variant: "default", needsAttention: false },
  error: { label: "Error", variant: "destructive", needsAttention: true },
  reauth_required: {
    label: "Reauthorization required",
    variant: "destructive",
    needsAttention: true,
  },
  disconnected: { label: "Disconnected", variant: "outline", needsAttention: false },
};

const FALLBACK: StatusPresentation = {
  label: "Unknown",
  variant: "outline",
  needsAttention: false,
};

/** Never throws on an unrecognized status: a backend that learns a new state
 *  should not blank the page for users on an older frontend build. */
export function statusPresentation(status: IntegrationStatus): StatusPresentation {
  return PRESENTATION[status] ?? FALLBACK;
}

export function statusLabel(status: IntegrationStatus): string {
  return statusPresentation(status).label;
}
