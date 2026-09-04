import { readFileSync } from "node:fs";

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TestConnectionButton } from "@/components/integrations/test-connection-button";
import { routerMock } from "@/vitest.setup";

function jsonResponse(body: unknown, ok = true, status = 200) {
  return { ok, status, text: async () => JSON.stringify(body) };
}

function entry(overrides: Record<string, unknown> = {}) {
  return {
    provider: "ga4",
    display_name: "Google Analytics 4",
    description: "",
    status: "connected",
    supports_resource_selection: true,
    connection: {
      id: 1,
      status: "connected",
      external_resource_id: "properties/111",
      external_resource_label: "acme.example",
      google_account_email: "",
      last_health_check_at: "2026-09-04T10:00:00Z",
      last_successful_check_at: "2026-09-04T10:00:00Z",
      last_error_code: "",
      last_error_message: "",
      created_at: "2026-09-01T10:00:00Z",
      updated_at: "2026-09-04T10:00:00Z",
    },
    ...overrides,
  };
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  document.cookie = "pi_csrftoken=test-csrf";
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("TestConnectionButton", () => {
  it("posts to the health-check endpoint for this project and provider", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(entry()));
    render(<TestConnectionButton projectId={7} provider="ga4" />);

    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/projects/7/integrations/ga4/health-check");
    expect(init.method).toBe("POST");
  });

  it("reports the outcome from the returned entry, not an invented one", async () => {
    // The check can complete and still report a problem. Announcing success
    // because the request returned 200 would tell the user the opposite of
    // what the backend just recorded.
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        entry({
          status: "error",
          connection: {
            ...entry().connection,
            status: "error",
            last_error_code: "resource_not_accessible",
            last_error_message: "That property is not available.",
          },
        }),
      ),
    );
    render(<TestConnectionButton projectId={7} provider="ga4" />);

    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(
        "That property is not available.",
      ),
    );
  });

  it("re-reads the page from the server after a check", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(entry()));
    render(<TestConnectionButton projectId={7} provider="ga4" />);

    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    await waitFor(() => expect(routerMock.refresh).toHaveBeenCalled());
  });

  it("surfaces a refused check without claiming anything about the connection", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        { error: { code: "resource_missing", message: "No property is selected." } },
        false,
        409,
      ),
    );
    render(<TestConnectionButton projectId={7} provider="ga4" />);

    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("No property is selected."),
    );
  });

  it("names no provider", () => {
    // `provider` goes into the request path and is never inspected.
    const source = readFileSync(
      "components/integrations/test-connection-button.tsx",
      "utf8",
    );
    expect(source).not.toMatch(/ga4|search_console|analytics|Search Console/);
  });
});
