import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TrackBand } from "./track-band";
import type { Period } from "@/lib/dashboard/period";

/**
 * Track as one card: what waits on the reader, the period's figures beside
 * it - the runs, what they cost, how many finished - and the usage hero
 * folded in, so the page no longer says them twice.
 */

const period: Period = { preset: "30d", from: "2026-07-07", to: "2026-08-05" };

const state = vi.hoisted(() => ({
  approvalsLoading: false,
  approvalsError: null as Error | null,
  approvalsCount: 0,
  threadsLoading: false,
  threadsError: null as Error | null,
  unreadThreads: 0,
  inboxLoading: false,
  inboxError: null as Error | null,
  unreadNotifications: 0,
  tasksPending: false,
  tasksError: null as Error | null,
  tasksDataMissing: false,
  activeTasks: 0,
  usageLoading: false,
  usageError: null as Error | null,
  usageMissing: false,
  totalRuns: 0,
  costUsd: "0",
  byStatus: [] as { status: string; runs: number }[],
}));

vi.mock("@/hooks", () => ({
  useAgentMessageThreads: () => ({
    threads: Array.from({ length: state.unreadThreads }, (_, i) => ({
      id: `t${i}`,
      unread: true,
    })),
    total: state.unreadThreads,
    isLoading: state.threadsLoading,
    error: state.threadsError,
    refetch: vi.fn(),
  }),
  useNotificationInbox: () => ({
    notifications: Array.from({ length: state.unreadNotifications }, (_, i) => ({
      id: `n${i}`,
      read_at: null,
    })),
    isLoading: state.inboxLoading,
    error: state.inboxError,
    refetch: vi.fn(),
    markRead: vi.fn(),
  }),
  useUsageStats: () => ({
    usage: state.usageMissing
      ? undefined
      : {
          total_runs: state.totalRuns,
          cost: { period_usd: state.costUsd },
          by_status: state.byStatus,
        },
    isLoading: state.usageLoading,
    error: state.usageError,
    refetch: vi.fn(),
  }),
}));

vi.mock("@/hooks/use-conversation-tasks", () => ({
  taskIsActive: () => true,
  useConversationTasks: () => ({
    data: state.tasksDataMissing
      ? undefined
      : Array.from({ length: state.activeTasks }, (_, i) => ({ id: `task${i}` })),
    isPending: state.tasksPending,
    error: state.tasksError,
    stop: { isPending: false, mutate: vi.fn() },
  }),
}));

vi.mock("@/hooks/use-pending-approvals", () => ({
  usePendingApprovals: () => ({
    tools: [],
    steps: [],
    count: state.approvalsCount,
    isLoading: state.approvalsLoading,
    error: state.approvalsError,
    refetch: vi.fn(),
  }),
}));

beforeEach(() => {
  state.approvalsLoading = false;
  state.approvalsError = null;
  state.approvalsCount = 0;
  state.threadsLoading = false;
  state.threadsError = null;
  state.unreadThreads = 0;
  state.inboxLoading = false;
  state.inboxError = null;
  state.unreadNotifications = 0;
  state.tasksPending = false;
  state.tasksError = null;
  state.tasksDataMissing = false;
  state.activeTasks = 0;
  state.usageLoading = false;
  state.usageError = null;
  state.usageMissing = false;
  state.totalRuns = 0;
  state.costUsd = "0";
  state.byStatus = [];
});

function renderBand() {
  return render(<TrackBand period={period} />);
}

describe("TrackBand", () => {
  it("waits for every source instead of reporting nothing waiting", () => {
    state.approvalsLoading = true;
    state.threadsLoading = true;
    state.inboxLoading = true;
    state.tasksPending = true;
    state.usageLoading = true;
    renderBand();
    expect(screen.getByRole("status", { name: "Loading" })).toBeInTheDocument();
    expect(screen.queryByText("Nothing waiting on you")).not.toBeInTheDocument();
  });

  it("is one link to Activity, with the waiting and the figures as rows", () => {
    state.approvalsCount = 2;
    state.unreadThreads = 1;
    state.unreadNotifications = 0;
    state.activeTasks = 0;
    state.totalRuns = 53;
    state.costUsd = "5.89";
    state.byStatus = [
      { status: "completed", runs: 48 },
      { status: "failed", runs: 5 },
    ];
    renderBand();

    const link = screen.getByRole("link");
    expect(link).toHaveAttribute("href", "/runs");
    expect(screen.getAllByRole("link")).toHaveLength(1);
    expect(screen.getByText("3 things waiting on you")).toBeInTheDocument();
    expect(screen.getByText("2 approvals · 1 message")).toBeInTheDocument();
    expect(screen.getByText("53 runs")).toBeInTheDocument();
    expect(screen.getByText("$5.89 spent")).toBeInTheDocument();
    expect(screen.getByText("91% finished")).toBeInTheDocument();
    expect(screen.getByText("of 53 runs")).toBeInTheDocument();
  });

  it("names the waiting line's other parts when they are the ones waiting", () => {
    state.unreadNotifications = 2;
    state.activeTasks = 1;
    state.totalRuns = 4;
    state.byStatus = [{ status: "completed", runs: 4 }];
    renderBand();

    expect(screen.getByText("3 things waiting on you")).toBeInTheDocument();
    expect(screen.getByText("2 notifications · 1 background task")).toBeInTheDocument();
  });

  it("says nothing is waiting, rather than counting zero, when the runs did happen", () => {
    state.totalRuns = 12;
    state.byStatus = [{ status: "completed", runs: 12 }];
    renderBand();

    expect(screen.getByText("Nothing waiting")).toBeInTheDocument();
    expect(screen.getByText("12 runs")).toBeInTheDocument();
    expect(screen.queryByText(/waiting on you/)).not.toBeInTheDocument();
  });

  it("a failed usage read still shows what waits", () => {
    state.usageError = new Error("offline");
    state.approvalsCount = 1;
    renderBand();

    expect(screen.getByText("1 thing waiting on you")).toBeInTheDocument();
    expect(screen.getByText("1 approval")).toBeInTheDocument();
    expect(screen.queryByText("Couldn't load this")).not.toBeInTheDocument();
  });

  it("a missing usage read still shows what waits, counting zero runs", () => {
    state.usageMissing = true;
    state.tasksDataMissing = true;
    state.approvalsCount = 1;
    renderBand();

    // No usage object at all: the rows still draw, with zero runs and a
    // dash where the finished share would be - unknown, not zero percent.
    expect(screen.getByText("1 thing waiting on you")).toBeInTheDocument();
    expect(screen.getByText("1 approval")).toBeInTheDocument();
    expect(screen.getByText("0 runs")).toBeInTheDocument();
    expect(screen.getByText("— finished")).toBeInTheDocument();
    expect(screen.getByText("of 0 runs")).toBeInTheDocument();
    expect(screen.queryByText("Couldn't load this")).not.toBeInTheDocument();
  });

  it("says every source failed, rather than showing an empty card", () => {
    state.usageError = new Error("offline");
    state.approvalsError = new Error("offline");
    state.threadsError = new Error("offline");
    state.inboxError = new Error("offline");
    state.tasksError = new Error("offline");
    renderBand();

    expect(screen.getByText("Couldn't load this")).toBeInTheDocument();
    expect(screen.queryByText("Nothing waiting on you")).not.toBeInTheDocument();
  });

  it("says there is nothing to track when nothing ran and nothing waits", () => {
    renderBand();

    expect(screen.getByText("Nothing waiting on you")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Approvals, agent messages, notifications and background tasks will show up here when they need you.",
      ),
    ).toBeInTheDocument();
  });
});
