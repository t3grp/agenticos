import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";
import { qk } from "@/lib/query-keys";
import { managedAppToolkit, useManagedAppConnect } from "./use-managed-app-connect";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), info: vi.fn() } }));
vi.mock("@/lib/locale-navigation", () => ({
  getPathname: ({
    locale,
    href,
  }: {
    locale: string;
    href: { pathname: string; query: Record<string, string> };
  }) => "/" + locale + href.pathname + "?" + new URLSearchParams(href.query),
}));
afterEach(() => vi.restoreAllMocks());

function setup() {
  const client = new QueryClient();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, ...renderHook(useManagedAppConnect, { wrapper }) };
}

describe("managed app routing", () => {
  it("opens a localized setup tab and refreshes paused-chat account observers on return", () => {
    const tab = { opener: {}, location: { href: "" } };
    const open = vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window);
    const { client, result } = setup();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    act(() => result.current("composio-gmail", { keepThisTab: true }));
    expect(open).toHaveBeenCalledWith("", "_blank");
    expect(tab.opener).toBeNull();
    expect(tab.location.href).toBe("/en/connectors?app=gmail");
    window.dispatchEvent(new Event("focus"));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: qk.mcpConnections.all() });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: qk.appConnections.all() });
  });

  it("preserves the paused tab when popups are blocked", () => {
    const before = window.location.href;
    vi.spyOn(window, "open").mockReturnValue(null);
    const { result } = setup();
    act(() => result.current("composio-outlook", { keepThisTab: true, sameTab: true }));
    expect(window.location.href).toBe(before);
    expect(toast.error).toHaveBeenCalled();
  });

  it("does not route arbitrary strings as managed apps", () => {
    expect(managedAppToolkit("notion")).toBeNull();
    expect(managedAppToolkit("composio-../../admin")).toBeNull();
    expect(managedAppToolkit(null)).toBeNull();
    expect(managedAppToolkit("composio-notion")).toBe("notion");
  });
});
