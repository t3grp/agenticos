import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { announceRoomChange } from "@/lib/room-events";
import { useConversationStore } from "@/stores";
import type { ChatMessage } from "@/types/chat";
import type {
  RoomAutoRoutingDecisionReceipt,
  RoomAutoRoutingDecisionsRead,
} from "@/types/room-auto-routing";
import { useRoomAutoRoutingDecisions } from "./use-room-auto-routing-decisions";

const state = vi.hoisted(() => ({
  accountId: "member-a" as string | undefined,
  organizationId: "org-a" as string | null,
  tenant: "org-a" as string | null,
  current: true,
  getDecisions: vi.fn(),
}));

vi.mock("@/hooks/use-room", () => ({
  useRoomIdentity: () => ({
    accountId: state.accountId,
    organizationId: state.organizationId,
    tenant: state.tenant,
    current: () => state.current,
  }),
}));
vi.mock("@/lib/room-auto-routing-api", () => ({
  getRoomAutoRoutingDecisions: state.getDecisions,
}));

let client: QueryClient;

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function message(id: string, overrides: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id,
    role: "user",
    content: `message ${id}`,
    timestamp: new Date("2026-11-20T06:00:00Z"),
    ...overrides,
  };
}

function receipt(
  acceptedId: string,
  groupIds: string[],
  status: RoomAutoRoutingDecisionReceipt["status"] = "selected",
): RoomAutoRoutingDecisionReceipt {
  return {
    accepted_message_id: acceptedId,
    grouped_message_ids: groupIds,
    status,
    router_agent_id: "router-a",
    router_name: "Room router",
    selected_agents: status === "selected" ? [{ agent_id: "agent-a", name: "Support" }] : [],
    accepted_at: "2026-11-20T06:00:00Z",
    finished_at: status === "pending" || status === "running" ? null : "2026-11-20T06:00:02Z",
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}

beforeEach(() => {
  state.accountId = "member-a";
  state.organizationId = "org-a";
  state.tenant = "org-a";
  state.current = true;
  state.getDecisions.mockReset().mockResolvedValue({ items: [] });
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  useConversationStore.setState({ currentConversationId: "room-a", selectionVersion: 4 });
});

describe("useRoomAutoRoutingDecisions", () => {
  it("reads saved human IDs and maps grouped receipts to their canonical message", async () => {
    const decision = receipt("saved-c", ["saved-a", "saved-b", "saved-c"]);
    state.getDecisions.mockResolvedValue({ items: [decision] });
    const messages = [
      message("saved-a"),
      message("local-b", { persistedId: "saved-b", isTemporaryId: true }),
      message("tmp-c", { isTemporaryId: true }),
      message("saved-c"),
      message("answer", { role: "assistant" }),
    ];

    const { result } = renderHook(() => useRoomAutoRoutingDecisions("room-a", true, messages), {
      wrapper,
    });

    await waitFor(() =>
      expect(result.current.receiptsByMessageId.get("saved-c")).toEqual(decision),
    );
    expect(state.getDecisions).toHaveBeenCalledExactlyOnceWith(
      "room-a",
      ["saved-a", "saved-b", "saved-c"],
      expect.anything(),
    );
  });

  it("places a canonical receipt on the earliest visible member when its accepted message is offscreen", async () => {
    const decision = receipt("saved-z", ["saved-a", "saved-b", "saved-z"]);
    state.getDecisions.mockResolvedValue({ items: [decision, decision] });

    const { result } = renderHook(
      () => useRoomAutoRoutingDecisions("room-a", true, [message("saved-b"), message("saved-a")]),
      { wrapper },
    );

    await waitFor(() =>
      expect(result.current.receiptsByMessageId.get("saved-a")).toEqual(decision),
    );
    expect(result.current.receiptsByMessageId.size).toBe(1);
    expect(state.getDecisions).toHaveBeenCalledWith(
      "room-a",
      ["saved-a", "saved-b"],
      expect.anything(),
    );
  });

  it("splits large transcript windows into API reads of at most 100 IDs", async () => {
    const messages = Array.from({ length: 101 }, (_, index) =>
      message(`message-${String(index).padStart(3, "0")}`),
    );

    renderHook(() => useRoomAutoRoutingDecisions("room-a", true, messages), { wrapper });

    await waitFor(() => expect(state.getDecisions).toHaveBeenCalledTimes(2));
    const batches = state.getDecisions.mock.calls.map((call) => call[1] as string[]);
    expect(batches.map((batch) => batch.length)).toEqual([100, 1]);
    expect(batches.flat().sort()).toEqual(messages.map((item) => item.id).sort());
  });

  it("does not query or expose receipts for a non-room transcript", () => {
    const { result } = renderHook(
      () => useRoomAutoRoutingDecisions("room-a", false, [message("saved-a")]),
      { wrapper },
    );

    expect(state.getDecisions).not.toHaveBeenCalled();
    expect(result.current.receiptsByMessageId.size).toBe(0);
    expect(result.current.error).toBeUndefined();
    expect(result.current.isLoading).toBe(false);
  });

  it("does not call the API when the identity scope is already stale", async () => {
    state.current = false;

    const { result } = renderHook(
      () => useRoomAutoRoutingDecisions("room-a", true, [message("saved-a")]),
      { wrapper },
    );

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(state.getDecisions).not.toHaveBeenCalled();
    expect(result.current.receiptsByMessageId.size).toBe(0);
  });

  it("drops a late response after the selected room changes", async () => {
    const pending = deferred<RoomAutoRoutingDecisionsRead>();
    state.getDecisions.mockReturnValueOnce(pending.promise);
    const { result } = renderHook(
      () => useRoomAutoRoutingDecisions("room-a", true, [message("saved-a")]),
      { wrapper },
    );
    await waitFor(() => expect(state.getDecisions).toHaveBeenCalledOnce());

    act(() =>
      useConversationStore.setState({ currentConversationId: "room-b", selectionVersion: 5 }),
    );
    await act(async () => {
      pending.resolve({ items: [receipt("saved-a", ["saved-a"])] });
      await pending.promise;
    });

    expect(result.current.receiptsByMessageId.size).toBe(0);
    expect(result.current.error).toBeUndefined();
  });

  it("invalidates only for matching room changes and can retry a failed read", async () => {
    const failure = new Error("unavailable");
    state.getDecisions.mockRejectedValueOnce(failure).mockResolvedValueOnce({ items: [] });
    const { result } = renderHook(
      () => useRoomAutoRoutingDecisions("room-a", true, [message("saved-a")]),
      { wrapper },
    );
    await waitFor(() => expect(result.current.error).toBe(failure));

    act(() =>
      announceRoomChange({
        conversationId: "room-a",
        accountId: "another-member",
        organizationId: "org-a",
      }),
    );
    expect(state.getDecisions).toHaveBeenCalledTimes(1);

    await act(async () => {
      await result.current.retry();
    });
    await waitFor(() => expect(state.getDecisions).toHaveBeenCalledTimes(2));
    expect(result.current.error).toBeUndefined();
  });

  it("refetches receipts for a matching persisted room change", async () => {
    const { result } = renderHook(
      () => useRoomAutoRoutingDecisions("room-a", true, [message("saved-a")]),
      { wrapper },
    );
    await waitFor(() => expect(state.getDecisions).toHaveBeenCalledOnce());

    act(() =>
      announceRoomChange({
        conversationId: "room-a",
        accountId: "member-a",
        organizationId: "org-a",
      }),
    );
    await waitFor(() => expect(state.getDecisions).toHaveBeenCalledTimes(2));
    expect(result.current.receiptsByMessageId.size).toBe(0);
  });
});
