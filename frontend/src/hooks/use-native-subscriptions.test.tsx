import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import { useNativeSubscriptions } from "./use-native-subscriptions";
import { useAuthStore } from "@/stores";
import { qk } from "@/lib/query-keys";
import * as api from "@/lib/native-subscriptions-api";
import type { NativeSubscriptionResult } from "@/types/native-subscriptions";

const scope = vi.hoisted(() => ({ tenant: "organization" as string | null }));
vi.mock("@/hooks/use-organizations", () => ({
  useTenantId: () => scope.tenant,
  useTenantGuard: () => (tenant: string | null) => scope.tenant === tenant,
}));
vi.mock("@/lib/native-subscriptions-api", () => ({
  getNativeSubscription: vi.fn(),
  changeNativeSubscription: vi.fn(),
}));
const status = {
  available: true,
  running: false,
  connected: false,
  auth_method: null,
  subscription_type: null,
  session_mode: null,
};
const person = {
  id: "person",
  email: "person@example.com",
  is_active: true,
  created_at: "2026-11-07T00:00:00Z",
};

function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, ...renderHook(() => useNativeSubscriptions(), { wrapper }) };
}

beforeEach(() => {
  vi.clearAllMocks();
  scope.tenant = "organization";
  useAuthStore.setState({ user: person });
  vi.mocked(api.getNativeSubscription).mockResolvedValue(status);
});

it("reads status without starting a native process", async () => {
  const { result } = mount();
  await waitFor(() => expect(result.current.status.isSuccess).toBe(true));
  expect(api.getNativeSubscription).toHaveBeenCalledWith("organization", expect.any(AbortSignal));
  expect(api.changeNativeSubscription).not.toHaveBeenCalled();
});

it.each(["organization", "person"])("does not query without a known %s", (missing) => {
  if (missing === "organization") scope.tenant = null;
  else useAuthStore.setState({ user: null });
  const { result } = mount();
  expect(result.current.ready).toBe(false);
  expect(api.getNativeSubscription).not.toHaveBeenCalled();
});

it("does not open another person's native session during impersonation", async () => {
  useAuthStore.setState({
    user: {
      ...person,
      impersonation: {
        session_id: "act",
        expires_at: "later",
        impersonator: { id: "admin", email: "admin@example.com" },
      },
    },
  });
  const { result } = mount();
  expect(result.current.ready).toBe(false);
  expect(result.current.impersonation).toBe(true);
  expect(await result.current.authorize()).toBeNull();
  expect(api.getNativeSubscription).not.toHaveBeenCalled();
  expect(api.changeNativeSubscription).not.toHaveBeenCalled();
});

it("updates only its own status and mints a new reauthorization ticket", async () => {
  const response: NativeSubscriptionResult = {
    status: { ...status, running: true },
    ticket: "fresh",
  };
  vi.mocked(api.changeNativeSubscription).mockResolvedValue(response);
  const { result, client } = mount();
  await waitFor(() => expect(result.current.status.isSuccess).toBe(true));
  await act(async () => {
    await result.current.change.mutateAsync({ action: "start" });
  });
  expect(client.getQueryData(qk.nativeSubscriptions.mine("organization:person:personal"))).toEqual(
    response.status,
  );
  expect(await result.current.authorize()).toBe("fresh");
  expect(api.changeNativeSubscription).toHaveBeenLastCalledWith("organization", {
    action: "ticket",
  });
});

it.each(["organization", "person"])("discards a late result after %s changes", async (change) => {
  let finish: ((result: NativeSubscriptionResult) => void) | undefined;
  vi.mocked(api.changeNativeSubscription).mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const { result, client, rerender } = mount();
  await waitFor(() => expect(result.current.status.isSuccess).toBe(true));
  const write = vi.spyOn(client, "setQueryData");
  let pending: Promise<NativeSubscriptionResult> | undefined;
  act(() => {
    pending = result.current.change.mutateAsync({ action: "login" });
  });
  await waitFor(() => expect(finish).toBeDefined());
  act(() => {
    if (change === "organization") scope.tenant = "other";
    else useAuthStore.setState({ user: null });
  });
  rerender();
  await act(async () => {
    finish?.({ status: { ...status, connected: true } });
    await pending;
  });
  expect(write).not.toHaveBeenCalled();
});

it("never reauthorizes an old terminal after changing organizations", async () => {
  let finish: ((result: NativeSubscriptionResult) => void) | undefined;
  vi.mocked(api.changeNativeSubscription).mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const { result, rerender } = mount();
  const pending = result.current.authorize();
  scope.tenant = "other";
  rerender();
  finish?.({ status, ticket: "wrong-scope" });
  expect(await pending).toBeNull();
});
