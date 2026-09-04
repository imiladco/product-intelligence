import { readFileSync } from "node:fs";

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DisconnectDialog } from "@/components/integrations/disconnect-dialog";
import { routerMock } from "@/vitest.setup";

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

function open() {
  render(
    <DisconnectDialog projectId={7} provider="ga4" providerName="Google Analytics 4" />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));
}

describe("DisconnectDialog", () => {
  it("asks for confirmation before touching anything", async () => {
    open();

    expect(screen.getByTestId("disconnect-dialog")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("says plainly what disconnecting does and does not do", () => {
    open();

    const dialog = screen.getByTestId("disconnect-dialog");
    const text = dialog.textContent ?? "";
    // Being vague here would be the security failure, not the UX one.
    expect(text).toMatch(/credential|access/i);
    expect(text).toMatch(/not revoke|does not revoke|remains|still/i);
    expect(text).toMatch(/remember|keep/i);
    const link = screen.getByRole("link", { name: /google account permissions/i });
    expect(link).toHaveAttribute("href", "https://myaccount.google.com/permissions");
    // Revoking there affects every integration sharing the authorization.
    expect(text).toMatch(/other integrations|every integration|any other/i);
  });

  it("posts to the disconnect endpoint once confirmed", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ provider: "ga4" }));
    open();

    fireEvent.click(screen.getByRole("button", { name: "Yes, disconnect" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/projects/7/integrations/ga4/disconnect");
    expect(init.method).toBe("POST");
  });

  it("re-reads the page from the server afterwards", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ provider: "ga4" }));
    open();

    fireEvent.click(screen.getByRole("button", { name: "Yes, disconnect" }));

    await waitFor(() => expect(routerMock.refresh).toHaveBeenCalled());
  });

  it("keeps the dialog open and reports a failure", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ error: { code: "oops", message: "Could not disconnect." } }, false, 400),
    );
    open();

    fireEvent.click(screen.getByRole("button", { name: "Yes, disconnect" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Could not disconnect."),
    );
  });

  it("names no provider", () => {
    const source = readFileSync(
      "components/integrations/disconnect-dialog.tsx",
      "utf8",
    );
    expect(source).not.toMatch(/ga4|search_console|analytics|Search Console/);
  });
});
