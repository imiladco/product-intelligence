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
      render(<IntegrationCard projectId={1} entry={entry()} />);

      expect(screen.getByText("Google Analytics 4")).toBeInTheDocument();
      expect(screen.getByText("Connect a GA4 property.")).toBeInTheDocument();
      expect(screen.getByTestId("status-badge")).toHaveTextContent("Not connected");
    });

    it("shows no resource or health detail", () => {
      render(<IntegrationCard projectId={1} entry={entry()} />);

      expect(screen.queryByText("Selected property")).not.toBeInTheDocument();
      expect(screen.queryByText("Last successful access")).not.toBeInTheDocument();
    });

    it("offers an enabled Connect action", () => {
      render(<IntegrationCard projectId={1} entry={entry()} />);

      const connect = screen.getByRole("button", { name: "Connect" });
      expect(connect).toBeEnabled();
    });
  });

  describe("connected", () => {
    it("shows the selected resource and last successful access", () => {
      render(
        <IntegrationCard projectId={1} entry={entry({ status: "connected", connection: connection() })} />,
      );

      expect(screen.getByTestId("status-badge")).toHaveTextContent("Connected");
      expect(screen.getByText("Acme Store — Web")).toBeInTheDocument();
      expect(screen.getByText("Selected property")).toBeInTheDocument();
      expect(screen.getByText("Last successful access")).toBeInTheDocument();
    });

    it("reads Never when a check has not happened yet", () => {
      render(
        <IntegrationCard
          projectId={1}
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
          projectId={1}
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
          projectId={1}
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
        <IntegrationCard projectId={1} entry={entry({ status: "connected", connection: connection() })} />,
      );
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });
  });

  it("never shows a credential-shaped value", () => {
    const { container } = render(
      <IntegrationCard projectId={1} entry={entry({ status: "connected", connection: connection() })} />,
    );
    const text = container.textContent ?? "";
    for (const forbidden of ["token", "secret", "refresh"]) {
      expect(text.toLowerCase()).not.toContain(forbidden);
    }
  });

  it("is identified by its provider", () => {
    render(<IntegrationCard projectId={1} entry={entry({ provider: "search_console" })} />);
    const card = screen.getByTestId("integration-card-search_console");
    expect(within(card).getByTestId("status-badge")).toBeInTheDocument();
  });
});

describe("IntegrationCard after OAuth", () => {
  it("renders Select a property for awaiting_resource_selection", () => {
    render(
      <IntegrationCard
        projectId={1}
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
    // Authorized is not connected: no property is chosen yet.
    expect(screen.getByTestId("status-badge")).not.toHaveTextContent("Connected");
    expect(
      screen.getByText("Choosing a property is not available yet."),
    ).toBeInTheDocument();
  });

  it("offers Reconnect once a connection exists", () => {
    render(
      <IntegrationCard
        projectId={1}
        entry={entry({
          status: "reauth_required",
          connection: connection({ status: "reauth_required" }),
        })}
      />,
    );
    expect(screen.getByRole("button", { name: "Reconnect" })).toBeEnabled();
  });

  it("keeps failure states useful", () => {
    render(
      <IntegrationCard
        projectId={1}
        entry={entry({
          status: "error",
          connection: connection({
            status: "error",
            last_error_code: "scope_not_granted",
            last_error_message:
              "The required read-only permission was not granted. Please connect again.",
          }),
        })}
      />,
    );

    expect(screen.getByTestId("status-badge")).toHaveTextContent("Error");
    expect(screen.getByRole("alert")).toHaveTextContent("read-only permission was not granted");
    // A retry is still offered.
    expect(screen.getByRole("button", { name: "Reconnect" })).toBeEnabled();
  });
});
