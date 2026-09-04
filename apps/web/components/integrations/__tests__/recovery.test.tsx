import { describe, expect, it } from "vitest";

import type { IntegrationStatus } from "@/lib/api/types";
import { RECOVERY_CLASS, presentationFor } from "@/lib/integrations/status";

/**
 * §7.1–7.3. Recovery is keyed on status *and* error code, because `error` is
 * reached by causes whose repairs have nothing to do with each other: a
 * deleted property and a declined OAuth scope both land there, and offering an
 * authorization action for the first is busywork that looks like a fix.
 */
describe("recovery classes", () => {
  it.each([
    ["credential_refresh_failed", "credential"],
    ["no_refresh_token", "credential"],
    ["scope_not_granted", "authorization"],
    ["token_exchange_failed", "authorization"],
    ["invalid_state", "authorization"],
    ["access_denied", "authorization"],
    ["provider_mismatch", "authorization"],
    ["oauth_error", "authorization"],
    ["resource_not_accessible", "resource"],
    ["resource_missing", "resource"],
    ["resource_unavailable", "transient"],
    ["google_api_error", "transient"],
  ] as const)("classifies %s as %s", (code, expected) => {
    expect(RECOVERY_CLASS[code]).toBe(expected);
  });

  it("interprets codes in exactly one place", () => {
    // The map is provider-neutral: no code names a property or a site.
    for (const code of Object.keys(RECOVERY_CLASS)) {
      expect(code).not.toMatch(/property|site|ga4|search_console/);
    }
  });
});

describe("the action matrix (§7.2)", () => {
  it("offers Connect and nothing else when there is no connection", () => {
    const p = presentationFor("not_connected", "");
    expect(p.actionLabel).toBe("Connect");
    expect(p.canTestConnection).toBe(false);
    expect(p.canDisconnect).toBe(false);
  });

  it("lets an abandoned authorization be restarted", () => {
    const p = presentationFor("pending_authorization", "");
    expect(p.actionLabel).toBe("Restart authorization");
    expect(p.canDisconnect).toBe(false);
  });

  it("asks for a selection when nothing is selected yet", () => {
    const p = presentationFor("awaiting_resource_selection", "");
    expect(p.primary).toBe("resource");
    expect(p.resourceAction).toBe("select");
    expect(p.actionLabel).toBeNull();
    expect(p.canDisconnect).toBe(true);
  });

  it("sends a resource-class failure to the picker, never to OAuth", () => {
    for (const status of ["awaiting_resource_selection", "error"] as const) {
      const p = presentationFor(status, "resource_not_accessible");
      expect(p.primary).toBe("resource");
      expect(p.resourceAction).toBe("change");
      // Re-authorizing a healthy credential fixes nothing.
      expect(p.actionLabel).toBeNull();
      expect(p.canTestConnection).toBe(true);
      expect(p.canDisconnect).toBe(true);
    }
  });

  it("suggests only checking again after a transient failure", () => {
    const p = presentationFor("connected", "resource_unavailable");
    expect(p.primary).toBe("test");
    // Nothing is broken: the badge stays green and the note is muted.
    expect(p.variant).toBe("default");
    expect(p.needsAttention).toBe(false);
    expect(p.errorTone).toBe("muted");
    expect(p.actionLabel).toBeNull();
  });

  it("offers reconnection when the grant itself is gone", () => {
    for (const status of ["error", "awaiting_resource_selection"] as const) {
      const p = presentationFor(status, "credential_refresh_failed");
      expect(p.actionLabel).toBe("Reconnect");
      expect(p.resourceAction).toBeNull();
      expect(p.canTestConnection).toBe(false);
    }
    expect(presentationFor("reauth_required", "").actionLabel).toBe("Reconnect");
  });

  it("offers Try again when the authorization itself failed", () => {
    const p = presentationFor("error", "scope_not_granted");
    expect(p.actionLabel).toBe("Try again");
    expect(p.resourceAction).toBeNull();
    expect(p.canTestConnection).toBe(false);
    expect(p.errorTone).toBe("destructive");
  });

  it("lets a healthy connection be checked, repointed or ended", () => {
    const p = presentationFor("connected", "");
    expect(p.primary).toBe("test");
    expect(p.canTestConnection).toBe(true);
    expect(p.resourceAction).toBe("change");
    expect(p.canDisconnect).toBe(true);
    expect(p.errorTone).toBeNull();
  });

  it("reconnects, and does not test, when reauthorization is required", () => {
    const p = presentationFor("reauth_required", "credential_refresh_failed");
    expect(p.canTestConnection).toBe(false);
    expect(p.canDisconnect).toBe(true);
  });

  it("offers Connect after a disconnect", () => {
    const p = presentationFor("disconnected", "");
    expect(p.actionLabel).toBe("Connect");
    expect(p.canDisconnect).toBe(false);
  });
});

describe("falling back safely", () => {
  it("degrades an unmapped code to the state's safe default", () => {
    // A new backend code nobody has mapped yet must not produce a
    // wrong-but-confident action.
    const p = presentationFor("error", "some_future_code");
    expect(p.actionLabel).toBe("Try again");
    expect(p.resourceAction).toBeNull();
  });

  it("degrades an unknown status rather than blanking the card", () => {
    const p = presentationFor("some_future_status" as IntegrationStatus, "");
    expect(p.label).toBe("Unknown");
    expect(p.actionLabel).toBeNull();
    expect(p.canTestConnection).toBe(false);
    expect(p.canDisconnect).toBe(false);
  });

  it("never offers an authorization and a resource action at once", () => {
    const statuses: IntegrationStatus[] = [
      "not_connected",
      "pending_authorization",
      "awaiting_resource_selection",
      "connected",
      "error",
      "reauth_required",
      "disconnected",
    ];
    const codes = ["", ...Object.keys(RECOVERY_CLASS), "some_future_code"];
    for (const status of statuses) {
      for (const code of codes) {
        const p = presentationFor(status, code);
        expect(p.actionLabel !== null && p.resourceAction !== null).toBe(false);
      }
    }
  });
});
