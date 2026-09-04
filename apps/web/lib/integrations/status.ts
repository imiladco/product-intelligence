import type { IntegrationStatus } from "@/lib/api/types";

type BadgeVariant = "default" | "secondary" | "destructive" | "outline";

/**
 * What kind of repair an error needs (§7.1).
 *
 * `error` is reached by causes whose fixes have nothing to do with each other:
 * a deleted property and a declined OAuth scope both land there, and offering
 * an authorization action for the first is busywork that looks like a fix. So
 * recovery is keyed on status *and* error code, and the code is interpreted
 * here and nowhere else.
 */
export type RecoveryClass = "credential" | "authorization" | "resource" | "transient";

/**
 * The only place an error code is interpreted.
 *
 * Provider-neutral by construction: every code names a credential, an
 * authorization or a resource, never a property or a site — which is what lets
 * one map serve both providers.
 */
export const RECOVERY_CLASS: Record<string, RecoveryClass> = {
  credential_refresh_failed: "credential",
  no_refresh_token: "credential",
  scope_not_granted: "authorization",
  token_exchange_failed: "authorization",
  invalid_state: "authorization",
  access_denied: "authorization",
  provider_mismatch: "authorization",
  oauth_error: "authorization",
  resource_not_accessible: "resource",
  resource_missing: "resource",
  resource_unavailable: "transient",
  google_api_error: "transient",
};

export interface StatusPresentation {
  /** Concise user-facing label. */
  label: string;
  variant: BadgeVariant;
  /** True when the state needs the user to do something. */
  needsAttention: boolean;
  /**
   * Label for the authorization action, or null when this state offers none.
   *
   * Only authorization actions live here. A state whose credential is fine
   * must not invite the user to re-authorize: it fixes nothing and teaches
   * them the button does not work.
   */
  actionLabel: string | null;
  /**
   * Whether this state offers a resource action, and which one.
   *
   * A separate channel from `actionLabel` on purpose: a connection can need a
   * property chosen without needing to be authorized again, and no state ever
   * offers both.
   */
  resourceAction: "select" | "change" | null;
  /** Whether an on-demand health check makes sense here. */
  canTestConnection: boolean;
  /** Whether there is anything to disconnect. */
  canDisconnect: boolean;
  /**
   * Which of the offered actions is the primary one.
   *
   * The flags above say what is *possible*; this says what to emphasize, which
   * the flags cannot express on their own — a connected card and a
   * resource-class error both offer Test connection and Change property, and
   * they lead with different ones.
   */
  primary: "authorization" | "resource" | "test" | null;
  /**
   * How a recorded error should read, or null when there is nothing to show.
   *
   * A transient failure on a working integration is a muted note beside a
   * still-green badge, not a destructive alert (§7.5). Components take this
   * from here rather than comparing codes themselves.
   */
  errorTone: "destructive" | "muted" | null;
  /** Truthful note about what is not possible yet, or null. */
  note: string | null;
}

/**
 * The single mapping from status to how it reads in the UI, before any error
 * code is taken into account.
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
    resourceAction: null,
    canTestConnection: false,
    canDisconnect: false,
    primary: "authorization",
    errorTone: null,
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
    resourceAction: null,
    canTestConnection: false,
    canDisconnect: false,
    primary: "authorization",
    errorTone: null,
    note: "Waiting for Google. If that window was closed, start again.",
  },
  awaiting_resource_selection: {
    label: "Select a property",
    variant: "secondary",
    needsAttention: true,
    // Google authorization already succeeded. The next step is choosing a
    // property, so this state must not invite re-authorizing.
    actionLabel: null,
    resourceAction: "select",
    canTestConnection: false,
    canDisconnect: true,
    primary: "resource",
    errorTone: null,
    note: null,
  },
  connected: {
    label: "Connected",
    variant: "default",
    needsAttention: false,
    actionLabel: null,
    resourceAction: "change",
    canTestConnection: true,
    canDisconnect: true,
    primary: "test",
    errorTone: null,
    note: null,
  },
  error: {
    label: "Error",
    variant: "destructive",
    needsAttention: true,
    actionLabel: "Try again",
    resourceAction: null,
    canTestConnection: false,
    canDisconnect: true,
    primary: "authorization",
    errorTone: "destructive",
    note: null,
  },
  reauth_required: {
    label: "Reauthorization required",
    variant: "destructive",
    needsAttention: true,
    actionLabel: "Reconnect",
    resourceAction: null,
    canTestConnection: false,
    canDisconnect: true,
    primary: "authorization",
    errorTone: "destructive",
    note: null,
  },
  disconnected: {
    label: "Disconnected",
    variant: "outline",
    needsAttention: false,
    actionLabel: "Connect",
    resourceAction: null,
    canTestConnection: false,
    canDisconnect: false,
    primary: "authorization",
    errorTone: null,
    note: null,
  },
};

const FALLBACK: StatusPresentation = {
  label: "Unknown",
  variant: "outline",
  needsAttention: false,
  actionLabel: null,
  resourceAction: null,
  canTestConnection: false,
  canDisconnect: false,
  primary: null,
  errorTone: null,
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

/**
 * How a state reads once its recorded error is taken into account (§7.2).
 *
 * The single entry point for recovery: components read the flags it returns
 * and never a status or an error code of their own. An unmapped code falls
 * back to the state's own default rather than guessing — a new backend code
 * degrades to "Try again", never to a wrong-but-confident action.
 */
export function presentationFor(
  status: IntegrationStatus,
  errorCode: string,
): StatusPresentation {
  const base = statusPresentation(status);
  const recovery = RECOVERY_CLASS[errorCode];
  if (recovery === undefined) {
    return base;
  }

  switch (recovery) {
    case "credential":
      // The grant is gone; only re-authorizing helps, and nothing else offered
      // here would do anything.
      return {
        ...base,
        actionLabel: "Reconnect",
        resourceAction: null,
        canTestConnection: false,
        primary: "authorization",
        errorTone: base.errorTone ?? "destructive",
      };

    case "authorization":
      // The authorization itself failed or was declined. Retrying it is the
      // fix; the resource is not the problem and must not be offered as one.
      return {
        ...base,
        actionLabel: "Try again",
        resourceAction: null,
        canTestConnection: false,
        primary: "authorization",
        errorTone: base.errorTone ?? "destructive",
      };

    case "resource":
      // The credential is fine; what it points at is not. Re-authorizing a
      // healthy credential is busywork that looks like a fix, so the picker is
      // primary and no authorization action is offered at all.
      return {
        ...base,
        actionLabel: null,
        resourceAction: "change",
        canTestConnection: true,
        primary: "resource",
        errorTone: base.errorTone ?? "destructive",
      };

    case "transient":
      // Nothing is broken; the last attempt did not land. Checking again is
      // the only thing worth suggesting, and the state keeps its own badge —
      // a blip on a working integration stays green.
      return {
        ...base,
        actionLabel: null,
        resourceAction: base.resourceAction === null ? null : "change",
        canTestConnection: true,
        primary: "test",
        errorTone: base.errorTone ?? "muted",
      };
  }
}
