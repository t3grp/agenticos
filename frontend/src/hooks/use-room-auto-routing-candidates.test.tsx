import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { RoomParticipantRead } from "@/types/room";
import { useRoomAutoRoutingCandidates } from "./use-room-auto-routing-candidates";

const state = vi.hoisted(() => ({
  agents: [] as unknown[],
  agentsLoading: false,
  agentsError: null as unknown,
  agentsRefetch: vi.fn(),
  participants: [] as unknown[],
  rosterLoading: false,
  rosterError: null as unknown,
  rosterRefetch: vi.fn(),
  roomId: null as string | null,
}));

vi.mock("@/hooks/use-agents", () => ({
  useAgents: () => ({
    agents: state.agents,
    isLoading: state.agentsLoading,
    error: state.agentsError,
    refetch: state.agentsRefetch,
  }),
}));
vi.mock("@/hooks/use-room-participants", () => ({
  useRoomParticipants: (conversationId: string | null) => {
    state.roomId = conversationId;
    return {
      participants: state.participants,
      isLoading: state.rosterLoading,
      error: state.rosterError,
      refetch: state.rosterRefetch,
    };
  },
}));

function participant(
  id: string,
  overrides: Partial<RoomParticipantRead> = {},
): RoomParticipantRead {
  return {
    kind: "agent",
    id,
    name: id,
    avatar_url: null,
    is_member: true,
    can_add: false,
    can_mention: true,
    auto_reply: false,
    ...overrides,
  };
}

function agent(id: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    status: "published",
    can_run: true,
    local_workspace: false,
    local_coding_agent: false,
    personal_coding_harness: false,
    ...overrides,
  };
}

beforeEach(() => {
  state.agents = [];
  state.agentsLoading = false;
  state.agentsError = null;
  state.agentsRefetch.mockReset().mockResolvedValue(undefined);
  state.participants = [];
  state.rosterLoading = false;
  state.rosterError = null;
  state.rosterRefetch.mockReset().mockResolvedValue(undefined);
  state.roomId = null;
});

describe("useRoomAutoRoutingCandidates", () => {
  it("keeps only current room members who are published, runnable hosted agents", () => {
    state.agents = [
      agent("mention-only"),
      agent("draft", { status: "draft" }),
      agent("cannot-run", { can_run: false }),
      agent("local-workspace", { local_workspace: true }),
      agent("local-coding", { local_coding_agent: true }),
      agent("personal-harness", { personal_coding_harness: true }),
      agent("not-in-room"),
    ];
    state.participants = [
      participant("mention-only"),
      participant("draft"),
      participant("cannot-run"),
      participant("local-workspace"),
      participant("local-coding"),
      participant("personal-harness"),
      participant("not-in-room", { is_member: false }),
      participant("person", { kind: "user" } as Partial<RoomParticipantRead>),
    ];

    const { result } = renderHook(() => useRoomAutoRoutingCandidates("room-a"));

    expect(state.roomId).toBe("room-a");
    expect(result.current.candidates.map((candidate) => candidate.id)).toEqual(["mention-only"]);
    expect(result.current.isLoading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("combines loading and exposes the roster error before the agent-list error", () => {
    const rosterError = new Error("room unavailable");
    state.rosterLoading = true;
    state.agentsLoading = true;
    state.rosterError = rosterError;
    state.agentsError = new Error("agents unavailable");

    const { result, rerender } = renderHook(() => useRoomAutoRoutingCandidates(null));

    expect(result.current.isLoading).toBe(true);
    expect(result.current.error).toBe(rosterError);
    state.rosterError = null;
    rerender();
    expect(result.current.error).toBe(state.agentsError);
  });

  it("retries both source queries together", async () => {
    const { result } = renderHook(() => useRoomAutoRoutingCandidates("room-a"));

    await result.current.retry();

    expect(state.rosterRefetch).toHaveBeenCalledExactlyOnceWith();
    expect(state.agentsRefetch).toHaveBeenCalledExactlyOnceWith();
  });
});
