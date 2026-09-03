import { readFileSync } from "node:fs";

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ResourcePickerDialog } from "@/components/integrations/resource-picker-dialog";
import { routerMock } from "@/vitest.setup";

const RESOURCES = {
  resources: [
    {
      id: "properties/111",
      label: "acme.example",
      group_label: "Acme Ltd",
      resource_type: "PROPERTY_TYPE_ORDINARY",
    },
    {
      id: "properties/222",
      label: "shop.acme.example",
      group_label: "Acme Ltd",
      resource_type: "PROPERTY_TYPE_ORDINARY",
    },
    {
      id: "properties/333",
      label: "other.example",
      group_label: "Other Holdings",
      resource_type: "PROPERTY_TYPE_ORDINARY",
    },
  ],
  truncated: false,
};

function jsonResponse(body: unknown, ok = true, status = 200) {
  return { ok, status, text: async () => JSON.stringify(body) };
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

async function openPicker(body: unknown = RESOURCES) {
  fetchMock.mockResolvedValueOnce(jsonResponse(body));
  render(<ResourcePickerDialog projectId={7} provider="ga4" providerName="Google Analytics 4" />);
  fireEvent.click(screen.getByRole("button", { name: "Choose property" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
}

describe("ResourcePickerDialog", () => {
  it("fetches nothing until it is opened", () => {
    render(<ResourcePickerDialog projectId={7} provider="ga4" providerName="Google Analytics 4" />);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("lists the properties the backend returned, grouped by account", async () => {
    await openPicker();

    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/projects/7/integrations/ga4/resources",
    );
    await screen.findByText("acme.example");
    expect(screen.getAllByText("Acme Ltd")).toHaveLength(1);
    expect(screen.getByText("Other Holdings")).toBeInTheDocument();
    expect(screen.getAllByRole("radio")).toHaveLength(3);
  });

  it("submits the identifier it was given, and no label", async () => {
    await openPicker();
    await screen.findByText("acme.example");
    fetchMock.mockResolvedValueOnce(jsonResponse({ provider: "ga4" }));

    fireEvent.click(screen.getAllByRole("radio")[1]);
    fireEvent.click(screen.getByRole("button", { name: "Use this property" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe("/api/projects/7/integrations/ga4/resource");
    expect(init.method).toBe("POST");
    // The browser is not a source of truth for what a property is called, so
    // it sends only the identifier.
    expect(JSON.parse(init.body)).toEqual({ resource_id: "properties/222" });
  });

  it("re-reads server state after a successful selection", async () => {
    await openPicker();
    await screen.findByText("acme.example");
    fetchMock.mockResolvedValueOnce(jsonResponse({ provider: "ga4" }));

    fireEvent.click(screen.getAllByRole("radio")[0]);
    fireEvent.click(screen.getByRole("button", { name: "Use this property" }));

    await waitFor(() => expect(routerMock.refresh).toHaveBeenCalled());
  });

  it("cannot submit before something is chosen", async () => {
    await openPicker();
    await screen.findByText("acme.example");

    expect(screen.getByRole("button", { name: "Use this property" })).toBeDisabled();
  });

  it("explains an empty account rather than showing an empty box", async () => {
    await openPicker({ resources: [], truncated: false });

    expect(
      await screen.findByText(/No properties are available/),
    ).toBeInTheDocument();
    expect(screen.queryAllByRole("radio")).toHaveLength(0);
  });

  it("says so when the list was truncated", async () => {
    await openPicker({ ...RESOURCES, truncated: true });

    expect(
      await screen.findByText(/Showing the first properties found/),
    ).toBeInTheDocument();
  });

  it("surfaces a load failure and offers a retry", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        { error: { code: "resource_unavailable", message: "Google could not be reached." } },
        false,
        503,
      ),
    );
    render(<ResourcePickerDialog projectId={7} provider="ga4" providerName="Google Analytics 4" />);
    fireEvent.click(screen.getByRole("button", { name: "Choose property" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Google could not be reached.",
    );

    fetchMock.mockResolvedValueOnce(jsonResponse(RESOURCES));
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByText("acme.example")).toBeInTheDocument();
  });

  it("keeps the dialog open when saving fails", async () => {
    await openPicker();
    await screen.findByText("acme.example");
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        {
          error: {
            code: "resource_not_accessible",
            message: "That property is not available.",
          },
        },
        false,
        400,
      ),
    );

    fireEvent.click(screen.getAllByRole("radio")[0]);
    fireEvent.click(screen.getByRole("button", { name: "Use this property" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(screen.getByTestId("resource-picker")).toBeInTheDocument();
    expect(routerMock.refresh).not.toHaveBeenCalled();
  });
});

describe("ResourcePickerDialog is provider-neutral", () => {
  const FLAT = {
    resources: [
      {
        id: "sc-domain:example.com",
        label: "sc-domain:example.com",
        group_label: "",
        resource_type: "Domain property",
      },
      {
        id: "https://shop.example.com/",
        label: "https://shop.example.com/",
        group_label: "",
        resource_type: "URL-prefix property",
      },
    ],
    truncated: false,
  };

  async function openFor(providerName: string, provider: string, body: unknown) {
    fetchMock.mockResolvedValueOnce(jsonResponse(body));
    render(
      <ResourcePickerDialog projectId={3} provider={provider} providerName={providerName} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Choose property" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  }

  it("renders a flat list with no heading when nothing is grouped", async () => {
    await openFor("Google Search Console", "search_console", FLAT);

    await screen.findByText("sc-domain:example.com");
    expect(screen.getAllByRole("radio")).toHaveLength(2);
    // No invented "Other" heading for a provider whose resources have no parent.
    expect(screen.queryByText("Other")).not.toBeInTheDocument();
    expect(document.querySelector("legend")).toBeNull();
  });

  it("renders headings when the data is grouped", async () => {
    await openFor("Google Analytics 4", "ga4", RESOURCES);

    await screen.findByText("acme.example");
    expect(document.querySelector("legend")).not.toBeNull();
    expect(screen.getByText("Acme Ltd")).toBeInTheDocument();
  });

  it("names the provider it was given in the empty state", async () => {
    await openFor("Google Search Console", "search_console", {
      resources: [],
      truncated: false,
    });

    expect(
      await screen.findByText(/No properties are available/),
    ).toBeInTheDocument();
    // Named in both the description and the empty state.
    expect(screen.getAllByText(/Google Search Console/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/Analytics/)).not.toBeInTheDocument();
  });

  it("posts to the provider it was given, without inspecting it", async () => {
    await openFor("Google Search Console", "search_console", FLAT);
    await screen.findByText("sc-domain:example.com");
    fetchMock.mockResolvedValueOnce(jsonResponse({ provider: "search_console" }));

    fireEvent.click(screen.getAllByRole("radio")[0]);
    fireEvent.click(screen.getByRole("button", { name: "Use this property" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe("/api/projects/3/integrations/search_console/resource");
    // The identifier goes back exactly as received — a URL is not parsed,
    // rebuilt or escaped by the browser.
    expect(JSON.parse(init.body)).toEqual({ resource_id: "sc-domain:example.com" });
  });

  it("contains no provider-specific terminology in its source", () => {
    // The neutrality claim, checked rather than intended: the component may
    // interpolate a provider name it is handed, but must not know that any
    // particular provider exists.
    // Vitest runs from apps/web, so this is stable without a file URL.
    const source = readFileSync(
      "components/integrations/resource-picker-dialog.tsx",
      "utf8",
    );
    for (const forbidden of ["Analytics", "GA4", "ga4", "sc-domain", "Search Console"]) {
      expect(source).not.toContain(forbidden);
    }
  });
});
