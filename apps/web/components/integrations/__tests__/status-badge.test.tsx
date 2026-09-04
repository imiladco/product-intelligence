import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusBadge } from "@/components/integrations/status-badge";
import type { IntegrationStatus } from "@/lib/api/types";
import { statusPresentation } from "@/lib/integrations/status";

/** Every status the API can report, stored or synthesized. */
const ALL_STATUSES: IntegrationStatus[] = [
  "not_connected",
  "pending_authorization",
  "awaiting_resource_selection",
  "connected",
  "error",
  "reauth_required",
  "disconnected",
];

describe("StatusBadge", () => {
  it.each([
    ["not_connected", "Not connected"],
    ["pending_authorization", "Connecting"],
    ["awaiting_resource_selection", "Select a property"],
    ["connected", "Connected"],
    ["error", "Error"],
    ["reauth_required", "Reauthorization required"],
    ["disconnected", "Disconnected"],
  ] as const)("renders %s as %s", (status, label) => {
    render(<StatusBadge status={status} />);
    expect(screen.getByTestId("status-badge")).toHaveTextContent(label);
  });

  it("renders every known status with a non-empty label", () => {
    for (const status of ALL_STATUSES) {
      expect(statusPresentation(status).label.trim()).not.toBe("");
    }
  });

  it("labels are distinct, so two states never read the same", () => {
    const labels = ALL_STATUSES.map((status) => statusPresentation(status).label);
    expect(new Set(labels).size).toBe(labels.length);
  });

  it("marks the states that need the user to act", () => {
    const attention = ALL_STATUSES.filter(
      (status) => statusPresentation(status).needsAttention,
    );
    expect(attention).toEqual([
      "awaiting_resource_selection",
      "error",
      "reauth_required",
    ]);
  });

  it("distinguishes choosing a property from changing one", () => {
    // Replaces the M2 assertion that connected offered no resource action at
    // all: changing a selection is this milestone's (§6), and the two are kept
    // as different actions because they read differently to the user.
    const selectable = ALL_STATUSES.filter(
      (status) => statusPresentation(status).resourceAction === "select",
    );
    expect(selectable).toEqual(["awaiting_resource_selection"]);
    expect(statusPresentation("connected").resourceAction).toBe("change");
  });

  it("keeps the authorization and resource actions separate", () => {
    // A state must never offer both: re-authorizing and choosing a property
    // answer different problems, and offering both makes neither clear.
    for (const status of ALL_STATUSES) {
      const { actionLabel, resourceAction } = statusPresentation(status);
      expect(actionLabel !== null && resourceAction !== null).toBe(false);
    }
  });

  it("falls back rather than breaking on an unrecognized status", () => {
    // A backend that learns a new state must not blank the page for users on
    // an older frontend build.
    const unknown = "some_future_status" as IntegrationStatus;
    render(<StatusBadge status={unknown} />);
    expect(screen.getByTestId("status-badge")).toHaveTextContent("Unknown");
    expect(statusPresentation(unknown).resourceAction).toBeNull();
  });
});
