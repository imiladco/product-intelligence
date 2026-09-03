import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ResourcePickerDialog } from "@/components/integrations/resource-picker-dialog";
import { routerMock } from "@/vitest.setup";

const RESOURCES = {
  resources: [
    {
      id: "properties/111",
      label: "acme.example",
      account_label: "Acme Ltd",
      property_type: "PROPERTY_TYPE_ORDINARY",
    },
    {
      id: "properties/222",
      label: "shop.acme.example",
      account_label: "Acme Ltd",
      property_type: "PROPERTY_TYPE_ORDINARY",
    },
    {
      id: "properties/333",
      label: "other.example",
      account_label: "Other Holdings",
      property_type: "PROPERTY_TYPE_ORDINARY",
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
  render(<ResourcePickerDialog projectId={7} provider="ga4" />);
  fireEvent.click(screen.getByRole("button", { name: "Choose property" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
}

describe("ResourcePickerDialog", () => {
  it("fetches nothing until it is opened", () => {
    render(<ResourcePickerDialog projectId={7} provider="ga4" />);
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
      await screen.findByText(/No Google Analytics properties are available/),
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
    render(<ResourcePickerDialog projectId={7} provider="ga4" />);
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
