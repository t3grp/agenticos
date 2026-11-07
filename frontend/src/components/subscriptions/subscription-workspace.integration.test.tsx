import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";
import { beforeEach, expect, it, vi } from "vitest";

import { SubscriptionWorkspace } from "./subscription-workspace";
import type {
  NativeSubscriptionResult,
  NativeSubscriptionStatus,
} from "@/types/native-subscriptions";

const mock = vi.hoisted(() => ({
  actions: {
    scope: "org:person:personal",
    ready: true,
    impersonation: false,
    status: {
      data: {} as NativeSubscriptionStatus,
      isPending: false,
      isError: false,
      error: null as unknown,
      refetch: vi.fn(),
    },
    change: { isPending: false, mutateAsync: vi.fn() },
    authorize: vi.fn(),
  },
  mountedTickets: [] as string[],
  closeTerminal: vi.fn(),
}));
vi.mock("@/hooks/use-native-subscriptions", () => ({ useNativeSubscriptions: () => mock.actions }));
vi.mock("./native-terminal", () => ({
  NativeTerminal: function MockTerminal({ ticket }: { ticket: string }) {
    useEffect(() => {
      mock.mountedTickets.push(ticket);
      return mock.closeTerminal;
    }, [ticket]);
    return <div data-testid="native-terminal" />;
  },
}));

const status: NativeSubscriptionStatus = {
  available: true,
  running: false,
  connected: false,
  auth_method: null,
  subscription_type: null,
  session_mode: null,
};
beforeEach(() => {
  vi.clearAllMocks();
  mock.mountedTickets = [];
  mock.actions.scope = "org:person:personal";
  mock.actions.ready = true;
  mock.actions.impersonation = false;
  Object.assign(mock.actions.status, {
    data: status,
    isPending: false,
    isError: false,
    error: null,
  });
  mock.actions.change.isPending = false;
  mock.actions.change.mutateAsync.mockResolvedValue({ status, ticket: "attachment" });
});

it("opens the native sign-in only after an explicit user action", async () => {
  render(<SubscriptionWorkspace />);
  expect(mock.actions.change.mutateAsync).not.toHaveBeenCalled();
  expect(screen.queryByTestId("native-terminal")).toBeNull();
  expect(
    screen.getByText(/Choose Sign in to open Claude Code's sign-in choices/),
  ).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect(mock.actions.change.mutateAsync).toHaveBeenCalledWith({
    action: "login",
    cols: 120,
    rows: 32,
  });
  expect(await screen.findByTestId("native-terminal")).toBeInTheDocument();
});

it("shows a failed read with retry instead of claiming the account is disconnected", async () => {
  mock.actions.status.isError = true;
  mock.actions.status.error = new Error("offline");
  render(<SubscriptionWorkspace />);
  expect(screen.getByText("Could not load your personal session")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Sign in" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Refresh status" }));
  expect(mock.actions.status.refetch).toHaveBeenCalledOnce();
});

it.each(["unavailable", "impersonation"])("does not start a session for %s", (reason) => {
  if (reason === "unavailable") mock.actions.status.data = { ...status, available: false };
  else mock.actions.impersonation = true;
  render(<SubscriptionWorkspace />);
  expect(screen.queryByRole("button", { name: "Sign in" })).toBeNull();
  expect(mock.actions.change.mutateAsync).not.toHaveBeenCalled();
});

it("confirms sign-out separately from stopping the native process", async () => {
  mock.actions.status.data = {
    ...status,
    connected: true,
    auth_method: "claude.ai",
    subscription_type: "max",
    running: true,
  };
  render(<SubscriptionWorkspace />);
  expect(screen.getByText("Subscription: max")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Stop session" }));
  expect(mock.actions.change.mutateAsync).toHaveBeenCalledWith({
    action: "stop",
    cols: 120,
    rows: 32,
  });
  await userEvent.click(screen.getByRole("button", { name: "Sign out of Claude" }));
  expect(mock.actions.change.mutateAsync).toHaveBeenCalledTimes(1);
  await userEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", { name: "Sign out of Claude" }),
  );
  expect(mock.actions.change.mutateAsync).toHaveBeenLastCalledWith({
    action: "sign_out",
    cols: 120,
    rows: 32,
  });
});

it.each(["api_key", "console", "third_party"])(
  "opens a connected %s account only after an explicit separate-billing choice",
  async (auth_method) => {
    mock.actions.status.data = { ...status, connected: true, auth_method };
    render(<SubscriptionWorkspace />);
    expect(
      screen.getByText(/This sign-in uses separate API or provider billing/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Choose Open with separate billing to continue/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Open session" })).toBeNull();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(mock.actions.change.mutateAsync).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Open with separate billing" }));
    expect(mock.actions.change.mutateAsync).toHaveBeenCalledWith({
      action: "start",
      cols: 120,
      rows: 32,
    });
    expect(await screen.findByTestId("native-terminal")).toBeInTheDocument();
  },
);

it("keeps the standard sign-in available to switch a separately billed account", async () => {
  mock.actions.status.data = { ...status, connected: true, auth_method: "api_key" };
  render(<SubscriptionWorkspace />);
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect(mock.actions.change.mutateAsync).toHaveBeenCalledWith({
    action: "login",
    cols: 120,
    rows: 32,
  });
});

it("guides a signed-in person to the available Open session control", () => {
  mock.actions.status.data = { ...status, connected: true, auth_method: "claude.ai" };
  render(<SubscriptionWorkspace />);
  expect(screen.getByRole("button", { name: "Open session" })).toBeInTheDocument();
  expect(
    screen.getByText(
      "You're signed in with your Claude subscription. Choose Open session to begin.",
    ),
  ).toBeInTheDocument();
  expect(screen.queryByText(/Choose Sign in to open Claude Code's sign-in choices/)).toBeNull();
  expect(mock.actions.change.mutateAsync).not.toHaveBeenCalled();
});

it("guides a returning person to reconnect to the running session", () => {
  mock.actions.status.data = { ...status, running: true, session_mode: "login" };
  render(<SubscriptionWorkspace />);
  expect(screen.getByRole("button", { name: "Reconnect" })).toBeInTheDocument();
  expect(
    screen.getByText("Choose Reconnect to continue your current Claude Code session."),
  ).toBeInTheDocument();
  expect(screen.queryByText(/Choose Sign in to open Claude Code's sign-in choices/)).toBeNull();
  expect(mock.actions.change.mutateAsync).not.toHaveBeenCalled();
});

it("disposes the old terminal when the account or organization scope changes", async () => {
  const { rerender } = render(<SubscriptionWorkspace />);
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  await screen.findByTestId("native-terminal");
  mock.actions.scope = "other:person:personal";
  rerender(<SubscriptionWorkspace />);
  expect(mock.closeTerminal).toHaveBeenCalledOnce();
  expect(screen.queryByTestId("native-terminal")).toBeNull();
});

it("does not attach a late sign-in result after switching scope", async () => {
  let finish: ((result: NativeSubscriptionResult) => void) | undefined;
  mock.actions.change.mutateAsync.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const { rerender } = render(<SubscriptionWorkspace />);
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  await waitFor(() => expect(finish).toBeDefined());
  mock.actions.scope = "other:person:personal";
  rerender(<SubscriptionWorkspace />);
  await act(async () => {
    finish?.({ status, ticket: "old-scope" });
  });
  expect(screen.queryByTestId("native-terminal")).toBeNull();
  expect(mock.mountedTickets).toEqual([]);
});
