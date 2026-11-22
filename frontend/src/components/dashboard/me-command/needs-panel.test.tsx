import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { NeedsPanel } from "./needs-panel";

/**
 * Needs you: the approvals waiting on a yes or no, the unread agent
 * messages, the background tasks still running. A zero is a row, not an
 * empty state.
 */

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
});

describe("NeedsPanel", () => {
  it("shows the three rows with their counts", () => {
    state.approvalsCount = 2;
    state.unreadThreads = 1;
    state.unreadNotifications = 2;
    state.activeTasks = 1;

    render(<NeedsPanel />);

    expect(screen.getByRole("heading", { name: "Needs you" })).toBeInTheDocument();
    expect(screen.getByText("Decisions only you can make.")).toBeInTheDocument();
    expect(screen.getByText("2 pending")).toBeInTheDocument();
    // Unread messages fan in from threads and notifications alike.
    expect(screen.getByText("3 unread messages")).toBeInTheDocument();
    expect(screen.getByText("1 still running")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open inbox/ })).toHaveAttribute("href", "/inbox");
  });

  it("a zero is a row saying nothing is waiting, not an empty panel", () => {
    render(<NeedsPanel />);

    expect(screen.getByText("Nothing waiting for a yes or no")).toBeInTheDocument();
    expect(screen.getByText("Nothing unread")).toBeInTheDocument();
    expect(screen.getByText("Nothing running")).toBeInTheDocument();
  });

  it("a single approval, message and task read in the singular", () => {
    state.approvalsCount = 1;
    state.unreadThreads = 1;
    state.activeTasks = 1;

    render(<NeedsPanel />);

    expect(screen.getByText("1 pending")).toBeInTheDocument();
    expect(screen.getByText("1 unread message")).toBeInTheDocument();
    expect(screen.getByText("1 still running")).toBeInTheDocument();
  });

  it("holds a skeleton while every source is still loading", () => {
    state.approvalsLoading = true;
    state.threadsLoading = true;
    state.inboxLoading = true;
    state.tasksPending = true;

    render(<NeedsPanel />);

    expect(screen.getByRole("status", { name: "Loading" })).toBeInTheDocument();
    expect(screen.queryByText("Approvals")).not.toBeInTheDocument();
  });

  it("draws one answer rather than waiting on the slowest source", () => {
    state.approvalsLoading = true;
    state.approvalsCount = 2;

    render(<NeedsPanel />);

    expect(screen.queryByRole("status", { name: "Loading" })).not.toBeInTheDocument();
    expect(screen.getByText("2 pending")).toBeInTheDocument();
  });

  it("says the request failed when every source failed", () => {
    state.approvalsError = new Error("down");
    state.threadsError = new Error("down");
    state.inboxError = new Error("down");
    state.tasksError = new Error("down");

    render(<NeedsPanel />);

    expect(screen.getByText("Couldn't load this")).toBeInTheDocument();
    expect(screen.queryByText("Approvals")).not.toBeInTheDocument();
  });

  it("a missing tasks list counts as no tasks, not a failure", () => {
    state.tasksDataMissing = true;

    render(<NeedsPanel />);

    expect(screen.getByText("Nothing running")).toBeInTheDocument();
  });
});
