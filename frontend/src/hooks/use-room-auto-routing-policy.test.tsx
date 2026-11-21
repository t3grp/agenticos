import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { ApiError } from "@/lib/api-error";
import { onRoomChange } from "@/lib/room-events";
import { useConversationStore } from "@/stores";
import type {
  RoomAutoRoutingPolicyDraft,
  RoomAutoRoutingPolicyRead,
} from "@/types/room-auto-routing";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useRoomAutoRoutingPolicy } from "./use-room-auto-routing-policy";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(),
  scope: { accountId: "member", organizationId: "org-a", tenant: "org-a" },
}));
let queryClient: QueryClient;

vi.mock("@/hooks/use-room", () => ({
  useRoomIdentity: () => {
    const accountId = mocks.scope.accountId;
    const organizationId = mocks.scope.organizationId;
    const tenant = mocks.scope.tenant;
    return {
      accountId,
      organizationId,
      tenant,
      current: () =>
        mocks.scope.accountId === accountId &&
        mocks.scope.organizationId === organizationId &&
        mocks.scope.tenant === tenant,
    };
  },
}));
vi.mock("@/lib/room-auto-routing-api", () => ({
  getRoomAutoRoutingPolicy: mocks.get,
  putRoomAutoRoutingPolicy: mocks.put,
}));

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}

function readPolicy(overrides: Partial<RoomAutoRoutingPolicyRead> = {}): RoomAutoRoutingPolicyRead {
  return {
    enabled: false,
    revision: 3,
    can_edit: true,
    router_agent_id: null,
    router_version_id: null,
    model_profile_id: null,
    quiet_window_ms: 2500,
    max_responders: 1,
    ...overrides,
  };
}

const draft: RoomAutoRoutingPolicyDraft = {
  enabled: true,
  router_agent_id: "agent-a",
  model_profile_id: "profile-a",
  quiet_window_ms: 3200,
  max_responders: 3,
};

beforeEach(() => {
  vi.clearAllMocks();
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  mocks.scope = { accountId: "member", organizationId: "org-a", tenant: "org-a" };
  useConversationStore.setState({ currentConversationId: "room-a", selectionVersion: 4 });
});

describe("useRoomAutoRoutingPolicy", () => {
  it("writes the complete selected policy against the revision that was read", async () => {
    const current = readPolicy();
    const saved = readPolicy({ ...draft, revision: 4, router_version_id: "version-a" });
    mocks.get.mockResolvedValueOnce(current).mockResolvedValue(saved);
    mocks.put.mockResolvedValue(saved);
    const { result } = renderHook(() => useRoomAutoRoutingPolicy("room-a"), { wrapper });
    await waitFor(() => expect(result.current.policy).toEqual(current));

    await act(async () => {
      expect(await result.current.savePolicy(draft)).toEqual(saved);
    });

    expect(mocks.put).toHaveBeenCalledWith("room-a", {
      ...draft,
      expected_revision: 3,
    });
    expect(result.current.policy).toEqual(saved);
  });

  it("drops a delayed write after selection changes A to B and back to A", async () => {
    const pending = deferred<RoomAutoRoutingPolicyRead>();
    const initial = readPolicy({ revision: 8 });
    const saved = readPolicy({ ...draft, revision: 9 });
    mocks.get.mockResolvedValue(initial);
    mocks.put.mockReturnValueOnce(pending.promise);
    const observed = vi.fn();
    const unsubscribe = onRoomChange(observed);
    const { result } = renderHook(() => useRoomAutoRoutingPolicy("room-a"), { wrapper });
    await waitFor(() => expect(result.current.policy).toEqual(initial));

    let operation!: Promise<RoomAutoRoutingPolicyRead | null>;
    act(() => {
      operation = result.current.savePolicy(draft);
    });
    act(() =>
      useConversationStore.setState({ currentConversationId: "room-b", selectionVersion: 5 }),
    );
    act(() =>
      useConversationStore.setState({ currentConversationId: "room-a", selectionVersion: 6 }),
    );

    await act(async () => {
      pending.resolve(saved);
      expect(await operation).toBeNull();
    });

    expect(observed).not.toHaveBeenCalled();
    await waitFor(() => expect(mocks.get).toHaveBeenCalledTimes(2));
    expect(result.current.policy).toEqual(initial);
    unsubscribe();
  });

  it("drops a delayed write when the active organization changes", async () => {
    const pending = deferred<RoomAutoRoutingPolicyRead>();
    const initial = readPolicy({ revision: 8 });
    const saved = readPolicy({ ...draft, revision: 9 });
    mocks.get.mockResolvedValue(initial);
    mocks.put.mockReturnValueOnce(pending.promise);
    const observed = vi.fn();
    const unsubscribe = onRoomChange(observed);
    const { result, rerender } = renderHook(() => useRoomAutoRoutingPolicy("room-a"), { wrapper });
    await waitFor(() => expect(result.current.policy).toEqual(initial));

    let operation!: Promise<RoomAutoRoutingPolicyRead | null>;
    act(() => {
      operation = result.current.savePolicy(draft);
    });
    mocks.scope.organizationId = "org-b";
    mocks.scope.tenant = "org-b";
    rerender();

    await act(async () => {
      pending.resolve(saved);
      expect(await operation).toBeNull();
    });

    expect(observed).not.toHaveBeenCalled();
    await waitFor(() => expect(result.current.policy).toEqual(initial));
    unsubscribe();
  });

  it("holds a stale-revision conflict until the current policy is reloaded", async () => {
    const initial = readPolicy({ revision: 11 });
    const latest = readPolicy({ revision: 12, quiet_window_ms: 4000 });
    const conflict = new ApiError(409, "stale revision", {
      error: { code: "ALREADY_EXISTS", message: "stale revision", details: null },
    });
    mocks.get.mockResolvedValueOnce(initial).mockResolvedValueOnce(latest);
    mocks.put.mockRejectedValueOnce(conflict);
    const { result } = renderHook(() => useRoomAutoRoutingPolicy("room-a"), { wrapper });
    await waitFor(() => expect(result.current.policy).toEqual(initial));

    await act(async () => {
      await expect(result.current.savePolicy(draft)).rejects.toBe(conflict);
    });
    expect(result.current.isConflict).toBe(true);

    await act(async () => {
      await result.current.refetch();
    });

    expect(result.current.policy).toEqual(latest);
    expect(result.current.isConflict).toBe(false);
  });
});
