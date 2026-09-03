import type { IntegrationStatus } from "@/lib/api/types";

type BadgeVariant = "default" | "secondary" | "destructive" | "outline";

interface StatusPresentation {
  /** Concise user-facing label. */
  label: string;
  variant: BadgeVariant;
  /** True when the state needs the user to do something. */
  needsAttention: boolean;
  /**
   * Label for the authorization action, or null when this state offers none.
   *
   * Only authorization actions live here. Reconnect and disconnect for a
   * healthy connection are a later milestone, and a state that already has
   * what it needs from Google must not invite the user to re-authorize.
   */
  actionLabel: string | null;
  /** Truthful note about what is not possible yet, or null. */
  note: string | null;
}

/**
 * The single mapping from status to how it reads in the UI.
 *
 * Centralized on purpose: components ask this module, and never compare status
 * strings themselves, so adding or renaming a state is one edit.
 */
const PRESENTATION: Record<IntegrationStatus, StatusPresentation> = {
  not_connected: {
    label: "Not connected",
    variant: "outline",
    needsAttention: false,
    actionLabel: "Connect",
    note: null,
  },
  pending_authorization: {
    label: "Connecting",
    variant: "secondary",
    needsAttention: false,
    // An authorization is in flight, but it can be abandoned — a closed tab, a
    // back button, lost connectivity — and nothing would then arrive at the
    // callback. Without a way to start over the user is stuck here for good,
    // so the action restarts the attempt. Not "Reconnect": nothing has
    // successfully connected yet.
    actionLabel: "Restart authorization",
    note: "Waiting for Google. If that window was closed, start again.",
  },
  awaiting_resource_selection: {
    label: "Select a property",
    variant: "secondary",
    needsAttention: true,
    // Google authorization already succeeded. The next step is choosing a
    // property, so this state must not invite re-authorizing.
    actionLabel: null,
    note: "Choosing a property is not available yet.",
  },
  connected: {
    label: "Connected",
    variant: "default",
    needsAttention: false,
    // Reconnect and disconnect for a healthy connection arrive later.
    actionLabel: null,
    note: null,
  },
  error: {
    label: "Error",
    variant: "destructive",
    needsAttention: true,
    actionLabel: "Try again",
    note: null,
  },
  reauth_required: {
    label: "Reauthorization required",
    variant: "destructive",
    needsAttention: true,
    actionLabel: "Reauthorize",
    note: null,
  },
  disconnected: {
    label: "Disconnected",
    variant: "outline",
    needsAttention: false,
    actionLabel: "Connect",
    note: null,
  },
};

const FALLBACK: StatusPresentation = {
  label: "Unknown",
  variant: "outline",
  needsAttention: false,
  actionLabel: null,
  note: null,
};

/** Never throws on an unrecognized status: a backend that learns a new state
 *  should not blank the page for users on an older frontend build. */
export function statusPresentation(status: IntegrationStatus): StatusPresentation {
  return PRESENTATION[status] ?? FALLBACK;
}

export function statusLabel(status: IntegrationStatus): string {
  return statusPresentation(status).label;
}
