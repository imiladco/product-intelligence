import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { IntegrationCard } from "@/components/integrations/integration-card";
import type { IntegrationEntry } from "@/lib/api/types";

/**
 * The exact shape GET /api/projects/{id}/integrations returns for a project
 * with nothing connected. The frontend renders whatever the backend catalog
 * says, and never keeps its own provider list.
 */
const API_RESPONSE: IntegrationEntry[] = [
  {
    provider: "ga4",
    display_name: "Google Analytics 4",
    description:
      "Connect a GA4 property to bring this project's traffic and behaviour data into the platform.",
    status: "not_connected",
    connection: null,
  },
  {
    provider: "search_console",
    display_name: "Google Search Console",
    description:
      "Connect a Search Console property to bring this project's search impressions, clicks and queries into the platform.",
    status: "not_connected",
    connection: null,
  },
];

function IntegrationList({ entries }: { entries: IntegrationEntry[] }) {
  return (
    <ul>
      {entries.map((entry) => (
        <li key={entry.provider}>
          <IntegrationCard entry={entry} />
        </li>
      ))}
    </ul>
  );
}

describe("integration list", () => {
  it("renders both providers from API-shaped data", () => {
    render(<IntegrationList entries={API_RESPONSE} />);

    expect(screen.getByTestId("integration-card-ga4")).toBeInTheDocument();
    expect(screen.getByTestId("integration-card-search_console")).toBeInTheDocument();
    expect(screen.getByText("Google Analytics 4")).toBeInTheDocument();
    expect(screen.getByText("Google Search Console")).toBeInTheDocument();
  });

  it("shows both as Not connected", () => {
    render(<IntegrationList entries={API_RESPONSE} />);

    const badges = screen.getAllByTestId("status-badge");
    expect(badges).toHaveLength(2);
    for (const badge of badges) {
      expect(badge).toHaveTextContent("Not connected");
    }
  });

  it("preserves the order the backend sent", () => {
    render(<IntegrationList entries={API_RESPONSE} />);

    const cards = screen.getAllByTestId(/^integration-card-/);
    expect(cards.map((card) => card.dataset.testid)).toEqual([
      "integration-card-ga4",
      "integration-card-search_console",
    ]);
  });

  it("renders a provider it has never seen before", () => {
    // The backend is the source of truth: a new catalog entry must appear
    // without a frontend change.
    render(
      <IntegrationList
        entries={[
          {
            provider: "future_provider",
            display_name: "Some Future Tool",
            description: "Added to the backend catalog.",
            status: "not_connected",
            connection: null,
          },
        ]}
      />,
    );

    expect(screen.getByText("Some Future Tool")).toBeInTheDocument();
    expect(screen.getByTestId("integration-card-future_provider")).toBeInTheDocument();
  });

  it("renders an empty catalog without crashing", () => {
    render(<IntegrationList entries={[]} />);
    expect(screen.queryAllByTestId("status-badge")).toHaveLength(0);
  });

  it("merges a stored connection into only its own provider", () => {
    render(
      <IntegrationList
        entries={[
          {
            ...API_RESPONSE[0],
            status: "connected",
            connection: {
              id: 7,
              status: "connected",
              external_resource_id: "properties/999",
              external_resource_label: "Acme — Web",
              google_account_email: "owner@example.com",
              last_health_check_at: "2026-09-01T10:00:00Z",
              last_successful_check_at: "2026-09-01T10:00:00Z",
              last_error_code: "",
              last_error_message: "",
              created_at: "2026-09-01T09:00:00Z",
              updated_at: "2026-09-01T10:00:00Z",
            },
          },
          API_RESPONSE[1],
        ]}
      />,
    );

    const ga4 = screen.getByTestId("integration-card-ga4");
    const gsc = screen.getByTestId("integration-card-search_console");
    expect(ga4).toHaveTextContent("Connected");
    expect(ga4).toHaveTextContent("Acme — Web");
    expect(gsc).toHaveTextContent("Not connected");
    expect(gsc).not.toHaveTextContent("Acme — Web");
  });
});
