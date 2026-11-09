import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiClient } from "@/lib/api-client";
import { useAuthStore, useOrgStore } from "@/stores";
import { useArchitectAskStore } from "@/stores/architect-ask-store";
import type { AppConnection, ConnectedAppDefinition } from "@/types/app-connections";
import { AppConnections } from "./app-connections";

const state = vi.hoisted(() => ({ assistantReady: true }));
vi.mock("@/lib/api-client", () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), delete: vi.fn(), patch: vi.fn() },
}));
vi.mock("@/hooks/use-assistant", () => ({
  useAssistant: () => ({
    assistant: {
      status: state.assistantReady ? "ready" : "disabled",
      agent_id: "assistant-1",
      can_use: state.assistantReady,
    },
    isLoading: false,
    error: null,
    retry: vi.fn(),
  }),
}));
const scope = { userId: "user-1", orgId: "org-1" };
const apps: ConnectedAppDefinition[] = [
  {
    key: "composio-googlecalendar",
    toolkit: "googlecalendar",
    name: "Google Calendar",
    description: "Meetings",
    category: "calendar",
    configured: true,
    actions: [],
  },
  {
    key: "composio-gmail",
    toolkit: "gmail",
    name: "Gmail",
    description: "Mail",
    category: "email",
    configured: true,
    actions: [],
  },
  {
    key: "composio-outlook",
    toolkit: "outlook",
    name: "Outlook",
    description: "Mail and meetings",
    category: "email",
    configured: true,
    actions: [],
  },
];
function account(overrides: Partial<AppConnection> = {}): AppConnection {
  return {
    id: "calendar-1",
    catalog_key: "composio-googlecalendar",
    toolkit: "googlecalendar",
    name: "Google Calendar",
    label: "Work",
    identity: "one@example.test",
    status: "connected",
    is_enabled: true,
    is_default: true,
    authorized: true,
    readiness: "authorization_verified",
    last_checked_at: null,
    last_error: null,
    created_at: "2026-01-01",
    ...overrides,
  };
}
function data(connections: AppConnection[] = [], configured = true) {
  vi.mocked(apiClient.get).mockImplementation(async (url) =>
    url.endsWith("/catalog")
      ? { configured, apps: apps.map((app) => ({ ...app, configured })) }
      : { items: connections, total: connections.length },
  );
}
function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AppConnections scope={scope} />
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  window.history.replaceState(null, "", "/connectors");
  state.assistantReady = true;
  useAuthStore.setState({
    user: {
      id: scope.userId,
      email: "one@example.test",
      is_active: true,
      created_at: "2026-01-01",
    },
  });
  useOrgStore.setState({ activeOrgId: scope.orgId });
  useArchitectAskStore.setState({ request: null });
  data();
});

describe("real app connection journey", () => {
  it("explicitly re-enables a locally disabled account without starting consent", async () => {
    data([account({ is_enabled: false, status: "disabled", authorized: false })]);
    vi.mocked(apiClient.patch).mockResolvedValue(account());
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "Enable" }));
    await waitFor(() =>
      expect(apiClient.patch).toHaveBeenCalledWith("/me/mcp-connections/calendar-1", {
        is_enabled: true,
      }),
    );
    expect(apiClient.post).not.toHaveBeenCalled();
    expect(await screen.findByText("Account enabled.")).toBeVisible();
  });

  it("selects a goal, offers its apps, and cancels before creating any authorization", async () => {
    mount();
    await userEvent.click(await screen.findByRole("button", { name: /Review my inbox/ }));
    await userEvent.click(screen.getByRole("button", { name: "Choose apps" }));
    expect(screen.queryByRole("button", { name: "Connect Google Calendar" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Connect Gmail" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/Review the exact permissions/)).toBeVisible();
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(apiClient.post).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByRole("button", { name: /Review my inbox/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("shows deployment configuration truth and prevents unconfigured authorization", async () => {
    data([], false);
    mount();
    expect(await screen.findByText("App connections are not configured yet")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Choose apps" }));
    expect(screen.getByRole("button", { name: "Connect Google Calendar" })).toBeDisabled();
    expect(apiClient.post).not.toHaveBeenCalled();
  });

  it("does not present a sample result or runnable task for an unverified account", async () => {
    data([account({ status: "pending", authorized: false, readiness: "authorization_required" })]);
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "Skip for now" }));
    expect(screen.getByText(/Connect and verify an app for this task first/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Prepare my day" })).toBeNull();
    expect(useArchitectAskStore.getState().request).toBeNull();
  });

  it("sends a real, read-only request for the selected account with user and workspace scope", async () => {
    data([
      account(),
      account({
        id: "calendar-2",
        identity: "two@example.test",
        label: "Personal",
        is_default: false,
      }),
    ]);
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "Skip for now" }));
    fireEvent.change(screen.getByRole("combobox", { name: "Use this account" }), {
      target: { value: "calendar-2" },
    });
    await userEvent.click(screen.getByRole("button", { name: "Prepare my day" }));
    const request = useArchitectAskStore.getState().request;
    expect(request?.scope).toEqual(scope);
    expect(request?.text).toContain("two@example.test");
    expect(request?.text).toContain("connection calendar-2");
    expect(request?.text).toContain("Read only");
    expect(request?.text).not.toContain("one@example.test");
    expect(screen.getByText(/Request sent to your assistant/)).toBeVisible();
  });

  it("does not offer a task when the assistant is disabled", async () => {
    data([account()]);
    state.assistantReady = false;
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "Skip for now" }));
    expect(screen.getByText(/Your assistant is not ready/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Prepare my day" })).toBeNull();
  });

  it("continues a chat-requested Gmail setup with a compatible first task", async () => {
    window.history.replaceState(null, "", "/connectors?app=gmail");
    data([
      account({ id: "gmail-1", toolkit: "gmail", catalog_key: "composio-gmail", name: "Gmail" }),
    ]);
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "Continue" }));
    await userEvent.click(screen.getByRole("button", { name: "Review my inbox" }));
    expect(useArchitectAskStore.getState().request?.text).toContain("connection gmail-1");
  });

  it("distinguishes labeled accounts with the same provider identity", async () => {
    data([account(), account({ id: "calendar-2", label: "Personal", is_default: false })]);
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "Skip for now" }));
    expect(
      screen.getByRole("option", { name: "Work · one@example.test · googlecalendar" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: "Personal · one@example.test · googlecalendar" }),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "calendar-2" } });
    await userEvent.click(screen.getByRole("button", { name: "Prepare my day" }));
    expect(useArchitectAskStore.getState().request?.text).toContain("Personal · one@example.test");
    expect(useArchitectAskStore.getState().request?.text).toContain("connection calendar-2");
  });

  it("verifies identity-only callbacks on the server and clears sensitive return parameters", async () => {
    window.history.replaceState(
      null,
      "",
      "/connectors?session_uri=identity-session&status=success",
    );
    vi.mocked(apiClient.post).mockResolvedValue(account());
    data([account()]);
    mount();
    expect(await screen.findByText("Your app authorization was verified.")).toBeVisible();
    expect(apiClient.post).toHaveBeenCalledWith("/me/app-connections/callback", {
      session_uri: "identity-session",
    });
    expect(window.location.search).toBe("");
  });

  it("does not turn a pending callback or forged success query into verified access", async () => {
    window.history.replaceState(null, "", "/connectors?connection_attempt=attempt&status=success");
    vi.mocked(apiClient.post).mockResolvedValue(
      account({ status: "pending", authorized: false, readiness: "unavailable" }),
    );
    mount();
    expect(await screen.findByText(/Authorization is not ready yet/)).toBeVisible();
    expect(screen.queryByText("Your app authorization was verified.")).toBeNull();
  });

  it("keeps a failed verification retryable without trusting the callback string", async () => {
    window.history.replaceState(null, "", "/connectors?session_uri=identity-session");
    vi.mocked(apiClient.post)
      .mockRejectedValueOnce(new Error("temporarily unavailable"))
      .mockResolvedValueOnce(account());
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "Verify again" }));
    expect(await screen.findByText("Your app authorization was verified.")).toBeVisible();
    expect(apiClient.post).toHaveBeenCalledTimes(2);
  });

  it("keeps authorization errors visible and never follows an unsafe redirect", async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ redirect_url: "javascript:alert(1)" });
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "Choose apps" }));
    await userEvent.click(screen.getByRole("button", { name: "Connect Google Calendar" }));
    await userEvent.click(screen.getByRole("button", { name: "Continue to app" }));
    expect(await screen.findByText(/invalid authorization address/)).toBeVisible();
    expect(window.location.pathname).toBe("/connectors");
  });

  it("confirms disconnect, refreshes status, and routes reconnection to the managed endpoint", async () => {
    const expired = account({
      status: "expired",
      authorized: false,
      readiness: "authorization_required",
    });
    data([expired]);
    vi.mocked(apiClient.post).mockResolvedValue({ redirect_url: "invalid" });
    vi.mocked(apiClient.delete).mockResolvedValue(undefined);
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "Reconnect" }));
    await userEvent.click(screen.getByRole("button", { name: "Continue to app" }));
    await screen.findByText(/invalid authorization address/);
    expect(apiClient.post).toHaveBeenCalledWith("/me/app-connections/calendar-1/reconnect", {});
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await userEvent.click(screen.getByRole("button", { name: "Check access" }));
    await waitFor(() =>
      expect(apiClient.post).toHaveBeenCalledWith("/me/app-connections/calendar-1/refresh", {}),
    );
    await userEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    expect(apiClient.delete).not.toHaveBeenCalled();
    await userEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: "Disconnect" }),
    );
    await waitFor(() =>
      expect(apiClient.delete).toHaveBeenCalledWith("/me/app-connections/calendar-1"),
    );
  });

  it("does not dispatch a task after its workspace changes", async () => {
    data([account()]);
    mount();
    await userEvent.click(await screen.findByRole("button", { name: "Skip for now" }));
    act(() => useOrgStore.setState({ activeOrgId: "other-org" }));
    await userEvent.click(screen.getByRole("button", { name: "Prepare my day" }));
    expect(useArchitectAskStore.getState().request).toBeNull();
  });
});
