import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { beforeEach, expect, it, vi } from "vitest";

import { useAuditFeed } from "./use-audit-feed";

const state = vi.hoisted(() => ({
  allowed: true,
  orgId: "o-1" as string | null,
  permissions: { isLoaded: true, isLoading: false, error: null as Error | null },
  refetchPermissions: vi.fn(),
  get: vi.fn(),
}));
vi.mock("@/hooks/use-permissions", () => ({
  usePermissions: () => ({
    can: () => state.allowed,
    ...state.permissions,
    refetch: state.refetchPermissions,
  }),
}));
vi.mock("@/hooks/use-organizations", () => ({
  useTenantId: () => state.orgId,
}));
vi.mock("@/lib/api-client", () => ({ apiClient: { get: state.get } }));

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return createElement(QueryClientProvider, { client }, children);
}

const PAGE = {
  items: [
    {
      id: "e1",
      sentence: "Architect used send email.",
      created_at: "2026-11-21T05:00:00Z",
    },
  ],
  total: 1,
};

beforeEach(() => {
  state.allowed = true;
  state.orgId = "o-1";
  state.permissions = { isLoaded: true, isLoading: false, error: null };
  state.refetchPermissions.mockReset();
  state.get.mockReset().mockResolvedValue(PAGE);
});

it("reads one page of the trail for the active organization", async () => {
  const { result } = renderHook(() => useAuditFeed(50), { wrapper });
  await waitFor(() => expect(result.current.isLoading).toBe(false));

  expect(result.current.entries).toEqual(PAGE.items);
  expect(result.current.total).toBe(1);
  expect(state.get).toHaveBeenCalledWith("/audit", {
    params: { skip: "50", limit: "50" },
  });
});

it("asks nothing for somebody who may not read the trail", () => {
  state.allowed = false;
  const { result } = renderHook(() => useAuditFeed(0), { wrapper });

  expect(result.current.entries).toEqual([]);
  expect(result.current.isLoading).toBe(false);
  expect(state.get).not.toHaveBeenCalled();
});

it("asks nothing while the tenant is unknown", () => {
  state.orgId = null;
  const { result } = renderHook(() => useAuditFeed(0), { wrapper });

  expect(state.get).not.toHaveBeenCalled();
  expect(result.current.entries).toEqual([]);
});

it("refetches the page once permissions are in, and the permissions before that", async () => {
  const { result } = renderHook(() => useAuditFeed(0), { wrapper });
  await waitFor(() => expect(result.current.isLoading).toBe(false));

  await result.current.refetch();
  expect(state.get).toHaveBeenCalledTimes(2);
  expect(state.refetchPermissions).not.toHaveBeenCalled();
});

it("refetches the permissions, not the page, while they are still loading", async () => {
  state.permissions = { isLoaded: false, isLoading: true, error: null };
  const { result } = renderHook(() => useAuditFeed(0), { wrapper });

  await result.current.refetch();
  expect(state.refetchPermissions).toHaveBeenCalledTimes(1);
});
