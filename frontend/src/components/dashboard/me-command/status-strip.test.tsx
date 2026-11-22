import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { StatusStrip } from "./status-strip";
import type { Period } from "@/lib/dashboard/period";

/**
 * The status strip: what waits, what waits on a decision, what ran and what
 * it cost - the four numbers the page answers to, on one line.
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
});

function renderStrip() {
  return render(<StatusStrip period={period} />);
}

describe("StatusStrip", () => {
  it("shows the four figures once they answer", () => {
    state.approvalsCount = 2;
    state.unreadThreads = 1;
    state.unreadNotifications = 2;
    state.activeTasks = 1;
    state.totalRuns = 40;
    state.costUsd = "1.64";

    renderStrip();

    // Waiting is the fan-in: 2 approvals + 1 thread + 2 notifications + 1 task.
    expect(screen.getByText("6")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("40")).toBeInTheDocument();
    expect(screen.getByText("$1.64")).toBeInTheDocument();
    expect(screen.getByText("waiting on you")).toBeInTheDocument();
    expect(screen.getByText("approvals pending")).toBeInTheDocument();
    expect(screen.getByText("runs")).toBeInTheDocument();
    expect(screen.getByText("spent")).toBeInTheDocument();
  });

  it("holds a skeleton while every source is still loading", () => {
    state.approvalsLoading = true;
    state.threadsLoading = true;
    state.inboxLoading = true;
    state.tasksPending = true;
    state.usageLoading = true;

    renderStrip();

    expect(screen.getByRole("status", { name: "Loading status" })).toBeInTheDocument();
    expect(screen.queryByText("waiting on you")).not.toBeInTheDocument();
  });

  it("draws one answer rather than waiting on the slowest source", () => {
    state.usageLoading = true;
    state.totalRuns = 40;
    state.costUsd = "1.64";

    renderStrip();

    expect(screen.queryByRole("status", { name: "Loading status" })).not.toBeInTheDocument();
    expect(screen.getByText("40")).toBeInTheDocument();
  });

  it("shows dashes when every source failed, not zeros", () => {
    state.approvalsError = new Error("down");
    state.threadsError = new Error("down");
    state.inboxError = new Error("down");
    state.tasksError = new Error("down");
    state.usageError = new Error("down");
    state.usageMissing = true;

    renderStrip();

    expect(screen.getAllByText("—")).toHaveLength(4);
    expect(screen.getByText("waiting on you")).toBeInTheDocument();
  });

  it("a missing tasks list counts as no tasks, not a failure", () => {
    state.tasksDataMissing = true;
    state.totalRuns = 3;

    renderStrip();

    // Waiting and approvals are both zero here, so the zero appears twice.
    expect(screen.getAllByText("0")).toHaveLength(2);
    expect(screen.getByText("3")).toBeInTheDocument();
  });
});
