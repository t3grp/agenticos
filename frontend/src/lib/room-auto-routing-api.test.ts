import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore, useOrgStore } from "@/stores";

import {
  getRoomAutoRoutingDecisions,
  getRoomAutoRoutingPolicy,
  putRoomAutoRoutingPolicy,
} from "./room-auto-routing-api";

function response(body: unknown, status = 200) {
  const text = JSON.stringify(body);
  return {
    ok: status < 400,
    status,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(text),
  } as Response;
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn().mockResolvedValue(response({}));
  vi.stubGlobal("fetch", fetchMock);
  useOrgStore.setState({ activeOrgId: "org-a" });
  useAuthStore.setState({ accessToken: null });
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function init(index = 0): RequestInit {
  return fetchMock.mock.calls[index]![1] as RequestInit;
}

describe("room automatic-routing API", () => {
  it("preserves the server's can_edit permission on policy reads", async () => {
    const policy = {
      enabled: true,
      revision: 3,
      can_edit: false,
      router_agent_id: "router-a",
      router_version_id: "version-a",
      model_profile_id: "profile-a",
      quiet_window_ms: 2500,
      max_responders: 2,
    };
    fetchMock.mockResolvedValueOnce(response(policy));

    await expect(getRoomAutoRoutingPolicy("room-a")).resolves.toEqual(policy);

    expect(fetchMock.mock.calls[0]![0]).toBe("/api/conversations/room-a/automatic-routing-policy");
    expect((init().headers as Record<string, string>)["X-Organization-Id"]).toBe("org-a");
  });

  it("sends unique repeated message IDs and keeps the receipt read through one stale-auth refresh", async () => {
    const receiptResponse = {
      items: [
        {
          accepted_message_id: "message-1",
          grouped_message_ids: ["message-1", "message-2"],
          status: "selected",
          router_agent_id: "router-a",
          router_name: "Room router",
          selected_agents: [{ agent_id: "agent-a", name: "Support" }],
          accepted_at: "2026-11-20T05:00:00Z",
          finished_at: "2026-11-20T05:00:02Z",
        },
      ],
    };
    fetchMock
      .mockResolvedValueOnce(response({ detail: "Token expired" }, 401))
      .mockResolvedValueOnce(response({ access_token: "fresh" }))
      .mockResolvedValueOnce(response(receiptResponse));

    await expect(
      getRoomAutoRoutingDecisions("room-a", ["message-2", "message-1", "message-1"]),
    ).resolves.toEqual(receiptResponse);

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/conversations/room-a/automatic-routing-decisions?message_id=message-1&message_id=message-2",
      "/api/auth/refresh",
      "/api/conversations/room-a/automatic-routing-decisions?message_id=message-1&message_id=message-2",
    ]);
    expect((init(2).headers as Record<string, string>)["X-Organization-Id"]).toBe("org-a");
  });

  it("sends the optimistic-concurrency revision with a policy write", async () => {
    const input = {
      expected_revision: 8,
      enabled: false,
      router_agent_id: null,
      model_profile_id: null,
      quiet_window_ms: 0,
      max_responders: 1,
    };
    fetchMock.mockResolvedValueOnce(response({ ...input, revision: 9, can_edit: true }));

    await putRoomAutoRoutingPolicy("room-a", input);

    expect(fetchMock.mock.calls[0]![0]).toBe("/api/conversations/room-a/automatic-routing-policy");
    expect(init().method).toBe("PUT");
    expect(JSON.parse(String(init().body))).toEqual(input);
  });

  it("rejects empty or oversized receipt reads before making a request", () => {
    expect(() => getRoomAutoRoutingDecisions("room-a", [])).toThrow(RangeError);
    expect(() => getRoomAutoRoutingDecisions("room-a", [""])).toThrow(RangeError);
    expect(() =>
      getRoomAutoRoutingDecisions(
        "room-a",
        Array.from({ length: 101 }, (_, i) => `m-${i}`),
      ),
    ).toThrow(RangeError);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
