import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConnectButton } from "@/components/integrations/connect-button";

const assign = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...window.location, assign },
  });
  document.cookie = "pi_csrftoken=test-csrf";
});

afterEach(() => {
  vi.unstubAllGlobals();
  assign.mockReset();
});

function mockJson(body: unknown, status = 200) {
  (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
    ok: status < 400,
    status,
    text: async () => JSON.stringify(body),
  });
}

describe("ConnectButton", () => {
  it("is enabled and labelled Connect", () => {
    render(<ConnectButton projectId={7} provider="ga4" />);
    expect(screen.getByRole("button", { name: "Connect" })).toBeEnabled();
  });

  it("asks the backend for the authorization URL at the right endpoint", async () => {
    mockJson({ authorization_url: "https://accounts.google.com/o/oauth2/v2/auth?x=1" });
    render(<ConnectButton projectId={7} provider="ga4" />);

    fireEvent.click(screen.getByRole("button", { name: "Connect" }));

    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    const [url, init] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/projects/7/integrations/ga4/authorize");
    expect(init.method ?? "GET").toBe("GET");
    expect(init.credentials).toBe("include");
  });

  it("uses the provider's own endpoint", async () => {
    mockJson({ authorization_url: "https://accounts.google.com/o/oauth2/v2/auth" });
    render(<ConnectButton projectId={3} provider="search_console" />);

    fireEvent.click(screen.getByRole("button", { name: "Connect" }));

    await waitFor(() =>
      expect((globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0]).toBe(
        "/api/projects/3/integrations/search_console/authorize",
      ),
    );
  });

  it("navigates to the URL the backend returned", async () => {
    const authUrl = "https://accounts.google.com/o/oauth2/v2/auth?client_id=abc";
    mockJson({ authorization_url: authUrl });
    render(<ConnectButton projectId={7} provider="ga4" />);

    fireEvent.click(screen.getByRole("button", { name: "Connect" }));

    await waitFor(() => expect(assign).toHaveBeenCalledWith(authUrl));
  });

  it("never builds an authorization URL itself", async () => {
    // Everything Google-specific comes from the backend: the frontend holds no
    // client id, no client secret, and no scope list.
    mockJson({ authorization_url: "https://accounts.google.com/o/oauth2/v2/auth" });
    render(<ConnectButton projectId={7} provider="ga4" />);

    fireEvent.click(screen.getByRole("button", { name: "Connect" }));

    await waitFor(() => expect(assign).toHaveBeenCalled());
    const requested = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(requested).not.toContain("accounts.google.com");
    expect(requested).not.toContain("client_id");
    expect(requested).not.toContain("scope");
  });

  it("recovers when the backend refuses", async () => {
    mockJson({ error: { code: "not_found", message: "Not found." } }, 404);
    render(<ConnectButton projectId={7} provider="ga4" />);

    fireEvent.click(screen.getByRole("button", { name: "Connect" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Connect" })).toBeEnabled(),
    );
    expect(assign).not.toHaveBeenCalled();
  });
});
