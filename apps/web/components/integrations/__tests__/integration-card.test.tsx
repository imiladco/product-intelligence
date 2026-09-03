import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

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
    expect(screen.getByRole("button", { name: "Choose property" })).toBeEnabled();
  });

  it("offers Reauthorize when authorization has become invalid", () => {
    render(
      <IntegrationCard
        projectId={1}
        entry={entry({
          status: "reauth_required",
          connection: connection({ status: "reauth_required" }),
        })}
      />,
    );
    expect(screen.getByRole("button", { name: "Reauthorize" })).toBeEnabled();
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
    // A truthful retry of the authorization is offered.
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
  });
});

describe("IntegrationCard action semantics", () => {
  /** Full reconnect/disconnect UX is a later milestone. A state that already
   *  has what it needs from Google must not invite re-authorizing. */
  function statusesOffering(name: string) {
    return (["not_connected", "pending_authorization", "awaiting_resource_selection",
      "connected", "error", "reauth_required", "disconnected"] as const).filter(
      (status) => {
        const { unmount } = render(
          <IntegrationCard
            projectId={1}
            entry={entry({
              status,
              connection: status === "not_connected" ? null : connection({ status }),
            })}
          />,
        );
        const found = screen.queryByRole("button", { name }) !== null;
        unmount();
        return found;
      },
    );
  }

  it("offers Connect only where nothing is authorized", () => {
    expect(statusesOffering("Connect")).toEqual(["not_connected", "disconnected"]);
  });

  it("never offers a generic Reconnect", () => {
    expect(statusesOffering("Reconnect")).toEqual([]);
  });

  it("does not invite re-authorizing after a successful authorization", () => {
    render(
      <IntegrationCard
        projectId={1}
        entry={entry({
          status: "awaiting_resource_selection",
          connection: connection({ status: "awaiting_resource_selection" }),
        })}
      />,
    );

    // The one action offered is choosing a property. Authorization already
    // succeeded, so nothing here suggests doing it again.
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Choose property" })).toBeEnabled();
    expect(screen.getByTestId("status-badge")).toHaveTextContent("Select a property");
  });

  it("offers a restart, not a duplicate Connect, while one is in flight", () => {
    // An abandoned flow never reaches the callback, so without this the user
    // would be stuck in pending_authorization permanently.
    render(
      <IntegrationCard
        projectId={1}
        entry={entry({
          status: "pending_authorization",
          connection: connection({ status: "pending_authorization" }),
        })}
      />,
    );

    expect(
      screen.getByRole("button", { name: "Restart authorization" }),
    ).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Connect" })).not.toBeInTheDocument();
    expect(
      screen.getByText("Waiting for Google. If that window was closed, start again."),
    ).toBeInTheDocument();
  });

  it("posts to the same authorize endpoint when restarting", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ authorization_url: "https://accounts.google.com/x" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const assign = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...window.location, assign },
    });
    document.cookie = "pi_csrftoken=test-csrf";

    render(
      <IntegrationCard
        projectId={9}
        entry={entry({
          provider: "search_console",
          status: "pending_authorization",
          connection: connection({ status: "pending_authorization" }),
        })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Restart authorization" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/projects/9/integrations/search_console/authorize");
    expect(init.method).toBe("POST");
    vi.unstubAllGlobals();
  });

  it("adds no reconnect or disconnect action to a connected integration", () => {
    render(
      <IntegrationCard
        projectId={1}
        entry={entry({ status: "connected", connection: connection() })}
      />,
    );

    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByTestId("status-badge")).toHaveTextContent("Connected");
  });
});
