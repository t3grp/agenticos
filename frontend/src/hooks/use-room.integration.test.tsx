import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiClient } from "@/lib/api-client";
import { ApiError } from "@/lib/api-error";
import { announceRoomChange, onRoomChange } from "@/lib/room-events";
import { qk } from "@/lib/query-keys";
import { useAuthStore, useChatStore, useConversationStore, useOrgStore } from "@/stores";
import type { RoomMessageResult } from "@/types/room";
import { useCreateTeamRoom, useRoomActions, useRoomRequests, useRoomSync } from "./use-room";
import { useRoomDraft } from "./use-room-draft";

vi.mock("@/lib/api-client", () => ({
  apiClient: { get: vi.fn(), post: vi.fn() },
}));

let client: QueryClient;
function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
const user = (id = "me") => ({
  id,
  email: `${id}@example.com`,
  is_active: true,
  created_at: "2026-11-13T00:00:00Z",
});
const saved: RoomMessageResult = {
  message: {
    id: "saved-message",
    conversation_id: "room-a",
    content: "Hello",
    role: "user",
    created_at: "2026-11-13T00:00:00Z",
  },
  request: null,
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  vi.clearAllMocks();
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  useAuthStore.setState({ user: user() });
  useOrgStore.setState({ activeOrgId: "org-a" });
  useConversationStore.setState({ currentConversationId: "room-a" });
  useChatStore.setState({ messages: [], replyTo: null });
  vi.mocked(apiClient.get).mockImplementation(async (path) =>
    path === "/orgs" ? { items: [] } : { items: [], total: 0, can_post: true },
  );
  vi.mocked(apiClient.post).mockResolvedValue(saved);
});
afterEach(() => client.clear());

describe("posting to a room", () => {
  it("posts a human message and reply without inferring an agent", async () => {
    useChatStore.setState({ replyTo: { messageId: "parent", role: "user", excerpt: "Earlier" } });
    const { result } = renderHook(() => useRoomDraft("room-a"), { wrapper });
    act(() => result.current.setContent("  Hello  "));
    await act(async () => result.current.submit());
    expect(apiClient.post).toHaveBeenCalledWith("/conversations/room-a/room-messages", {
      client_message_id: expect.any(String),
      content: "Hello",
      reply_to_message_id: "parent",
    });
    expect(result.current.content).toBe("");
    expect(useChatStore.getState().replyTo).toBeNull();
  });

  it("retries an uncertain acknowledgement with the same id, text, responder and reply", async () => {
    useChatStore.setState({ replyTo: { messageId: "parent", role: "user", excerpt: "Earlier" } });
    vi.mocked(apiClient.post).mockRejectedValueOnce(new TypeError("Network lost"));
    const { result } = renderHook(() => useRoomDraft("room-a"), { wrapper });
    act(() => {
      result.current.setContent("Please check");
      result.current.setAgentId("agent-a");
    });
    await act(async () => result.current.submit());
    const first = vi.mocked(apiClient.post).mock.calls[0]![1];
    expect(result.current.retrying).toBe(true);
    expect(result.current.locked).toBe(true);
    expect(result.current.content).toBe("Please check");
    act(() =>
      useChatStore.setState({
        replyTo: { messageId: "another", role: "user", excerpt: "A new reply" },
      }),
    );
    await act(async () => result.current.submit());
    expect(vi.mocked(apiClient.post).mock.calls[1]![1]).toEqual(first);
    expect(first).toMatchObject({
      content: "Please check",
      agent_id: "agent-a",
      reply_to_message_id: "parent",
    });
    expect(result.current.content).toBe("");
    expect(result.current.agentId).toBeNull();
    expect(useChatStore.getState().replyTo?.messageId).toBe("another");
  });

  it("keeps an editable draft after a definite validation refusal", async () => {
    vi.mocked(apiClient.post).mockRejectedValueOnce(new ApiError(422, "Reply no longer exists"));
    const { result } = renderHook(() => useRoomDraft("room-a"), { wrapper });
    act(() => result.current.setContent("Hello"));
    await act(async () => result.current.submit());
    expect(result.current.content).toBe("Hello");
    expect(result.current.locked).toBe(false);
    expect(result.current.error).toBeTruthy();
  });

  it("retains an uncertain token when a later retry is refused after access changes", async () => {
    vi.mocked(apiClient.post)
      .mockRejectedValueOnce(new TypeError("Acknowledgement lost"))
      .mockRejectedValueOnce(new ApiError(403, "Access changed"));
    const { result } = renderHook(() => useRoomDraft("room-a"), { wrapper });
    act(() => result.current.setContent("Do this once"));
    await act(async () => result.current.submit());
    const first = vi.mocked(apiClient.post).mock.calls[0]![1];
    await act(async () => result.current.submit());
    expect(result.current.locked).toBe(true);
    expect(result.current.retrying).toBe(true);
    await act(async () => result.current.submit());
    expect(vi.mocked(apiClient.post).mock.calls[2]![1]).toEqual(first);
  });

  it("admits only one in-flight click", async () => {
    const pending = deferred<RoomMessageResult>();
    vi.mocked(apiClient.post).mockReturnValue(pending.promise);
    const { result } = renderHook(() => useRoomDraft("room-a"), { wrapper });
    act(() => result.current.setContent("Hello"));
    let posting!: Promise<void>;
    act(() => {
      posting = result.current.submit();
      void result.current.submit();
    });
    expect(apiClient.post).toHaveBeenCalledTimes(1);
    await act(async () => {
      pending.resolve(saved);
      await posting;
    });
    expect(result.current.content).toBe("");
  });

  it.each(["conversation", "account", "organization"] as const)(
    "ignores a late acknowledgement after the %s changes",
    async (boundary) => {
      const pending = deferred<RoomMessageResult>();
      vi.mocked(apiClient.post).mockReturnValue(pending.promise);
      const changed = vi.fn();
      const unsubscribe = onRoomChange(changed);
      const { result } = renderHook(() => useRoomActions("room-a"), { wrapper });
      const posting = result.current.post({ client_message_id: "stable-id", content: "Hello" });
      act(() => {
        if (boundary === "conversation")
          useConversationStore.setState({ currentConversationId: "room-b" });
        if (boundary === "account") useAuthStore.setState({ user: user("other") });
        if (boundary === "organization") useOrgStore.setState({ activeOrgId: "org-b" });
        useChatStore.setState({ replyTo: { messageId: "b-reply", role: "user", excerpt: "B" } });
      });
      client.setQueryData(qk.roomRequests.list("me", "org-a", "room-b"), { items: ["keep-b"] });
      pending.resolve(saved);
      await expect(posting).resolves.toBeNull();
      expect(changed).not.toHaveBeenCalled();
      expect(useChatStore.getState().replyTo?.messageId).toBe("b-reply");
      expect(client.getQueryData(qk.roomRequests.list("me", "org-a", "room-b"))).toEqual({
        items: ["keep-b"],
      });
      unsubscribe();
    },
  );

  it("drops a failed response after the form unmounts", async () => {
    const pending = deferred<RoomMessageResult>();
    vi.mocked(apiClient.post).mockReturnValue(pending.promise);
    const { result, unmount } = renderHook(() => useRoomActions("room-a"), { wrapper });
    const posting = result.current.post({ client_message_id: "one", content: "Hello" });
    unmount();
    pending.reject(new Error("old request"));
    await expect(posting).resolves.toBeNull();
  });

  it("does not start a request for a room that is no longer selected", async () => {
    const { result } = renderHook(() => useRoomActions("room-a"), { wrapper });
    act(() => useConversationStore.setState({ currentConversationId: "room-b" }));
    await expect(
      result.current.post({ client_message_id: "one", content: "Hello" }),
    ).resolves.toBeNull();
    expect(apiClient.post).not.toHaveBeenCalled();
  });
});

it("refreshes authoritative posting access on a matching room event only", async () => {
  let canPost = true;
  vi.mocked(apiClient.get).mockImplementation(async (path) =>
    path === "/orgs" ? { items: [] } : { items: [], total: 0, can_post: canPost },
  );
  const { result } = renderHook(() => useRoomRequests("room-a"), { wrapper });
  await waitFor(() => expect(result.current.data?.can_post).toBe(true));
  canPost = false;
  act(() =>
    announceRoomChange({ conversationId: "room-a", accountId: "other", organizationId: "org-a" }),
  );
  expect(result.current.data?.can_post).toBe(true);
  act(() =>
    announceRoomChange({ conversationId: "room-a", accountId: "me", organizationId: "org-a" }),
  );
  await waitFor(() => expect(result.current.data?.can_post).toBe(false));
});

it("refreshes a human message while preserving an unsaved streaming answer", async () => {
  vi.useFakeTimers();
  const human = { id: "human", role: "user" as const, content: "Question", timestamp: new Date() };
  const live = {
    id: "live",
    role: "assistant" as const,
    content: "Still writing",
    timestamp: new Date(),
    isStreaming: true,
  };
  useChatStore.setState({ messages: [human, live] });
  const reread = vi.fn(async () => {
    useChatStore
      .getState()
      .rereadMessages([
        human,
        { id: "teammate", role: "user", content: "An update", timestamp: new Date() },
      ]);
  });
  const { unmount } = renderHook(() => useRoomSync("room-a", reread), { wrapper });
  try {
    act(() =>
      announceRoomChange({ conversationId: "room-a", accountId: "me", organizationId: "org-a" }),
    );
    await act(async () => vi.advanceTimersByTimeAsync(150));
    expect(useChatStore.getState().messages).toEqual(expect.arrayContaining([live]));
    expect(useChatStore.getState().messages.map((message) => message.id)).toContain("teammate");
    expect(reread).toHaveBeenCalledWith("room-a");
  } finally {
    unmount();
    vi.useRealTimers();
  }
});

it("creates an empty project room without invoking an agent", async () => {
  vi.mocked(apiClient.post).mockResolvedValueOnce({ id: "new-room", project_id: "project-a" });
  const { result } = renderHook(() => useCreateTeamRoom(), { wrapper });
  await act(async () =>
    expect(result.current("project-a", "Our team")).resolves.toMatchObject({ id: "new-room" }),
  );
  expect(apiClient.post).toHaveBeenCalledWith("/conversations", {
    project_id: "project-a",
    title: "Our team",
  });
});

it("cancels a queued request through its room endpoint", async () => {
  const { result } = renderHook(() => useRoomActions("room-a"), { wrapper });
  await act(async () => result.current.cancel("request-a"));
  expect(apiClient.post).toHaveBeenCalledWith(
    "/conversations/room-a/room-requests/request-a/cancel",
    {},
  );
});
