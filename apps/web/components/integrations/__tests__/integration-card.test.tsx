import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { IntegrationCard } from "@/components/integrations/integration-card";
import type { IntegrationConnection, IntegrationEntry } from "@/lib/api/types";

function connection(overrides: Partial<IntegrationConnection> = {}): IntegrationConnection {
  return {
    id: 1,
    status: "connected",
    external_resource_id: "properties/123456",
    external_resource_label: "Acme Store — Web",
    google_account_email: "owner@example.com",
    last_health_check_at: "2026-09-01T10:00:00Z",
    last_successful_check_at: "2026-09-01T10:00:00Z",
    last_error_code: "",
    last_error_message: "",
    created_at: "2026-09-01T09:00:00Z",
    updated_at: "2026-09-01T10:00:00Z",
    ...overrides,
  };
}

function entry(overrides: Partial<IntegrationEntry> = {}): IntegrationEntry {
  return {
    provider: "ga4",
    display_name: "Google Analytics 4",
    description: "Connect a GA4 property.",
    status: "not_connected",
    connection: null,
    ...overrides,
  };
}

describe("IntegrationCard", () => {
  describe("not connected", () => {
    it("shows the provider identity, description and status", () => {
      render(<IntegrationCard entry={entry()} />);

      expect(screen.getByText("Google Analytics 4")).toBeInTheDocument();
      expect(screen.getByText("Connect a GA4 property.")).toBeInTheDocument();
      expect(screen.getByTestId("status-badge")).toHaveTextContent("Not connected");
    });

    it("shows no resource or health detail", () => {
      render(<IntegrationCard entry={entry()} />);

      expect(screen.queryByText("Selected property")).not.toBeInTheDocument();
      expect(screen.queryByText("Last successful access")).not.toBeInTheDocument();
    });

    it("offers a Connect action that is disabled until OAuth exists", () => {
      // Milestone 2 must never let a click produce a connected state.
      render(<IntegrationCard entry={entry()} />);

      const connect = screen.getByRole("button", { name: "Connect" });
      expect(connect).toBeDisabled();
      expect(
        screen.getByText("Connecting Google accounts is not available yet."),
      ).toBeInTheDocument();
    });
  });

  describe("connected", () => {
    it("shows the selected resource and last successful access", () => {
      render(
        <IntegrationCard entry={entry({ status: "connected", connection: connection() })} />,
      );

      expect(screen.getByTestId("status-badge")).toHaveTextContent("Connected");
      expect(screen.getByText("Acme Store — Web")).toBeInTheDocument();
      expect(screen.getByText("Selected property")).toBeInTheDocument();
      expect(screen.getByText("Last successful access")).toBeInTheDocument();
    });

    it("reads Never when a check has not happened yet", () => {
      render(
        <IntegrationCard
          entry={entry({
            status: "awaiting_resource_selection",
            connection: connection({
              status: "awaiting_resource_selection",
              external_resource_label: "",
              last_health_check_at: null,
              last_successful_check_at: null,
            }),
          })}
        />,
      );

      expect(screen.getByTestId("status-badge")).toHaveTextContent("Select a property");
      expect(screen.getByText("None selected")).toBeInTheDocument();
      expect(screen.getAllByText("Never")).toHaveLength(2);
    });
  });

  describe("error and reauthorization", () => {
    it("shows the error message alongside the last time it worked", () => {
      render(
        <IntegrationCard
          entry={entry({
            status: "error",
            connection: connection({
              status: "error",
              last_error_code: "resource_inaccessible",
              last_error_message:
                "The selected property is no longer accessible to the authorized Google account.",
            }),
          })}
        />,
      );

      expect(screen.getByTestId("status-badge")).toHaveTextContent("Error");
      expect(screen.getByRole("alert")).toHaveTextContent(
        "no longer accessible to the authorized Google account",
      );
      // The last successful access survives a failure.
      expect(screen.getByText("Last successful access")).toBeInTheDocument();
    });

    it("shows the reauthorization state", () => {
      render(
        <IntegrationCard
          entry={entry({
            status: "reauth_required",
            connection: connection({ status: "reauth_required" }),
          })}
        />,
      );

      expect(screen.getByTestId("status-badge")).toHaveTextContent(
        "Reauthorization required",
      );
    });

    it("renders no alert when there is no error", () => {
      render(
        <IntegrationCard entry={entry({ status: "connected", connection: connection() })} />,
      );
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });
  });

  it("never shows a credential-shaped value", () => {
    const { container } = render(
      <IntegrationCard entry={entry({ status: "connected", connection: connection() })} />,
    );
    const text = container.textContent ?? "";
    for (const forbidden of ["token", "secret", "refresh"]) {
      expect(text.toLowerCase()).not.toContain(forbidden);
    }
  });

  it("is identified by its provider", () => {
    render(<IntegrationCard entry={entry({ provider: "search_console" })} />);
    const card = screen.getByTestId("integration-card-search_console");
    expect(within(card).getByTestId("status-badge")).toBeInTheDocument();
  });
});
