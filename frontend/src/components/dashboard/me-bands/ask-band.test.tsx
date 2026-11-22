import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AskBand } from "./ask-band";
import type { Period } from "@/lib/dashboard/period";

/**
 * Ask as one card: the recent conversations, the most-run agents where
 * there are none yet, and the shared shelf for a caller who may not run
 * agents - each a preview, chat the one destination.
 */

const period: Period = { preset: "30d", from: "2026-07-07", to: "2026-08-05" };

const state = vi.hoisted(() => ({
  canRun: true,
  convLoading: false,
  convError: null as Error | null,
  conversations: [] as {
    id: string;
    title: string | null;
    updated_at: string | null;
    created_at: string;
  }[],
  usageLoading: false,
  usageError: null as Error | null,
  usageMissing: false,
  byAgent: [] as { agent_id: string; name: string; runs: number }[],
  listLoading: false,
  listError: null as Error | null,
  agents: [] as { id: string; name: string; owner_user_id: string | null }[],
  can: (_permission: string) => false as boolean,
  userId: "u1" as string | null,
  convEnabled: null as boolean | null,
  usageEnabled: null as boolean | null,
  listEnabled: null as boolean | null,
}));

vi.mock("@/hooks", () => ({
  useRecentConversations: (_limit: number, options?: { enabled?: boolean }) => {
    state.convEnabled = options?.enabled ?? true;
    return {
      conversations: state.conversations,
      isLoading: state.convLoading,
      error: state.convError,
      refetch: vi.fn(),
    };
  },
  useUsageStats: (_period: unknown, options?: { enabled?: boolean }) => {
    state.usageEnabled = options?.enabled ?? true;
    return {
      usage: state.usageMissing ? undefined : { by_agent: state.byAgent },
      isLoading: state.usageLoading,
      error: state.usageError,
      refetch: vi.fn(),
    };
  },
  useAgents: (options?: { enabled?: boolean }) => {
    state.listEnabled = options?.enabled ?? true;
    return {
      agents: state.agents,
      isLoading: state.listLoading,
      error: state.listError,
      refetch: vi.fn(),
    };
  },
  usePermissions: () => ({ can: state.can }),
}));

vi.mock("@/stores", () => ({
  useAuthStore: (selector: (value: unknown) => unknown) =>
    selector({ user: state.userId === null ? null : { id: state.userId } }),
}));

beforeEach(() => {
  state.canRun = true;
  state.convLoading = false;
  state.convError = null;
  state.conversations = [];
  state.usageLoading = false;
  state.usageError = null;
  state.usageMissing = false;
  state.byAgent = [];
  state.listLoading = false;
  state.listError = null;
  state.agents = [];
  state.can = () => false;
  state.userId = "u1";
  state.convEnabled = null;
  state.usageEnabled = null;
  state.listEnabled = null;
});

function renderBand() {
  return render(<AskBand period={period} canRun={state.canRun} />);
}

describe("AskBand", () => {
  it("waits for the conversations instead of reporting none", () => {
    state.convLoading = true;
    renderBand();
    expect(screen.getByRole("status", { name: "Loading" })).toBeInTheDocument();
    expect(screen.queryByText("No conversations yet")).not.toBeInTheDocument();
  });

  it("is one link to chat, previewing the recent conversations", () => {
    state.conversations = [
      {
        id: "c1",
        title: "Q3 pipeline summary",
        updated_at: "2026-08-05T08:00:00Z",
        created_at: "2026-08-05T08:00:00Z",
      },
      {
        id: "c2",
        title: null,
        updated_at: "2026-08-04T08:00:00Z",
        created_at: "2026-08-04T08:00:00Z",
      },
    ];
    renderBand();

    const link = screen.getByRole("link");
    expect(link).toHaveAttribute("href", "/chat");
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(screen.getByText("Q3 pipeline summary")).toBeInTheDocument();
    // An untitled thread is named, not left blank.
    expect(screen.getByText("Untitled conversation")).toBeInTheDocument();
    expect(state.convEnabled).toBe(true);
    expect(state.usageEnabled).toBe(true);
    expect(state.listEnabled).toBe(false);
  });

  it("falls back to the most-run agents where there are no conversations", () => {
    state.byAgent = [
      { agent_id: "a1", name: "Timesheet Tracker", runs: 21 },
      { agent_id: "a2", name: "Architect", runs: 9 },
    ];
    renderBand();

    expect(screen.getByText("Timesheet Tracker")).toBeInTheDocument();
    expect(screen.getByText("21 runs")).toBeInTheDocument();
    expect(screen.getByText("Architect")).toBeInTheDocument();
    expect(screen.queryByText("No conversations yet")).not.toBeInTheDocument();
  });

  it("a failed conversations read still shows the agents that did answer", () => {
    state.convError = new Error("offline");
    state.byAgent = [{ agent_id: "a1", name: "Timesheet Tracker", runs: 21 }];
    renderBand();

    expect(screen.getByText("Timesheet Tracker")).toBeInTheDocument();
    expect(screen.queryByText("Couldn't load this")).not.toBeInTheDocument();
  });

  it("says both sources failed, rather than showing an empty card", () => {
    state.convError = new Error("offline");
    state.usageError = new Error("offline");
    renderBand();

    expect(screen.getByText("Couldn't load this")).toBeInTheDocument();
    expect(screen.queryByText("No conversations yet")).not.toBeInTheDocument();
  });

  it("says there is nothing to continue when neither source has anything", () => {
    renderBand();

    expect(screen.getByText("No conversations yet")).toBeInTheDocument();
    expect(
      screen.getByText("Start one from an agent card - it will be waiting here."),
    ).toBeInTheDocument();
  });

  it("shows a viewer the agents shared with them, and asks no usage question", () => {
    state.canRun = false;
    state.can = (permission: string) => permission === "agents:view";
    state.agents = [
      { id: "a1", name: "Support triage", owner_user_id: "u2" },
      { id: "a2", name: "My draft", owner_user_id: "u1" },
    ];
    renderBand();

    // Only what was shared: the caller's own draft is not on this shelf.
    expect(screen.getByText("Support triage")).toBeInTheDocument();
    expect(screen.getByText("shared")).toBeInTheDocument();
    expect(screen.queryByText("My draft")).not.toBeInTheDocument();
    expect(state.convEnabled).toBe(false);
    expect(state.usageEnabled).toBe(false);
    expect(state.listEnabled).toBe(true);
  });

  it("says nothing is shared with a viewer who has no agents", () => {
    state.canRun = false;
    renderBand();

    expect(screen.getByText("No agents yet")).toBeInTheDocument();
  });

  it("falls back to the created date where a conversation has no update", () => {
    state.conversations = [
      {
        id: "c1",
        title: "Q3 pipeline summary",
        updated_at: null,
        created_at: "2026-08-04T08:00:00Z",
      },
    ];
    renderBand();

    expect(screen.getByText("Q3 pipeline summary")).toBeInTheDocument();
    expect(screen.getByRole("link")).toHaveAttribute("href", "/chat");
  });

  it("says there is nothing to continue when the usage read comes back empty", () => {
    state.usageMissing = true;
    renderBand();

    expect(screen.getByText("No conversations yet")).toBeInTheDocument();
  });

  it("shows a viewer the shared shelf even with no signed-in user", () => {
    state.canRun = false;
    state.userId = null;
    state.can = (permission: string) => permission === "agents:view";
    state.agents = [{ id: "a1", name: "Support triage", owner_user_id: "u2" }];
    renderBand();

    expect(screen.getByText("Support triage")).toBeInTheDocument();
    expect(screen.getByText("shared")).toBeInTheDocument();
  });

  it("a failed agents list is a failure, not an empty shelf, for a viewer", () => {
    state.canRun = false;
    state.listError = new Error("offline");
    renderBand();

    expect(screen.getByText("Couldn't load this")).toBeInTheDocument();
  });
});
