import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";

import messages from "../../../../messages/en.json";
import { TooltipProvider } from "@/components/ui";
import type { Period } from "@/lib/dashboard/period";
import type { WorkflowApprovalRead } from "@/lib/workflows/types";
import type { AgentMessageThreadRead } from "@/types/agent-messages";
import type { Notification } from "@/lib/notifications-api";
import type { ConversationTask } from "@/hooks/use-conversation-tasks";
import type { ToolApproval } from "@/types/runs";
import { WaitingOnMeWidget } from "./waiting-on-me";

const usePendingMock = vi.fn();
const useThreadsMock = vi.fn();
const useInboxMock = vi.fn();
const useTasksMock = vi.fn();

vi.mock("@/hooks", () => ({
  useAgentMessageThreads: (...args: unknown[]) => useThreadsMock(...args),
  useNotificationInbox: (...args: unknown[]) => useInboxMock(...args),
  useAgents: () => ({
    agents: [{ id: "a1", name: "Architect", slug: "architect", has_avatar: false }],
  }),
}));
vi.mock("@/hooks/use-conversation-tasks", () => ({
  useConversationTasks: (...args: unknown[]) => useTasksMock(...args),
  // The real predicate: queued, running, awaiting_approval, awaiting_answer.
  taskIsActive: (task: { status: string }) =>
    ["queued", "running", "awaiting_approval", "awaiting_answer"].includes(task.status),
}));
vi.mock("@/hooks/use-pending-approvals", () => ({
  usePendingApprovals: (...args: unknown[]) => usePendingMock(...args),
}));
vi.mock("@/stores", () => ({ useArchitectMarkStore: () => false }));
vi.mock("@/lib/utils", async (original) => ({
  ...(await original<typeof import("@/lib/utils")>()),
  timeAgo: (value: string) => `at ${value}`,
}));

const PERIOD: Period = { preset: "30d", from: "2026-10-15", to: "2026-11-14" };

function toolApproval(overrides: Partial<ToolApproval> = {}): ToolApproval {
  return {
    id: "ap1",
    run_id: "r1",
    agent_id: "a1",
    agent_name: "Architect",
    tool_id: "write_memory",
    tool_args: {},
    triggered_by_email: null,
    subagent_name: null,
    subagent_agent_id: null,
    status: "pending",
    decided_by_user_id: null,
    decided_at: null,
    decided_via: "click",
    note: null,
    created_at: "2026-11-14T08:00:00Z",
    ...overrides,
  };
}

function stepApproval(overrides: Partial<WorkflowApprovalRead> = {}): WorkflowApprovalRead {
  return {
    id: "step-1",
    workflow_id: "w1",
    workflow_name: "Weekly report",
    workflow_run_id: "wr1",
    node_run_id: "nr1",
    title: "Send the draft to the team?",
    details: null,
    approver_user_ids: [],
    status: "pending",
    expires_at: null,
    decided_by_user_id: null,
    decided_at: null,
    note: null,
    created_at: "2026-11-14T08:00:00Z",
    editable: false,
    proposal: null,
    proposal_format: "text",
    proposal_label: null,
    final: null,
    edited: false,
    choices: [],
    choice: null,
    ...overrides,
  };
}

function thread(
  index: number,
  overrides: Partial<AgentMessageThreadRead> = {},
): AgentMessageThreadRead {
  return {
    id: `thread-${index}`,
    agent_a: { id: `a-${index}`, slug: `first-${index}`, name: `First ${index}` },
    agent_b: { id: `b-${index}`, slug: `second-${index}`, name: `Second ${index}` },
    last_message_at: "2026-11-14T08:00:00Z",
    last_message: {
      sender_agent_slug: `first-${index}`,
      body: `Review ${index}`,
      created_at: "2026-11-14T08:00:00Z",
    },
    unread: true,
    created_at: "2026-11-14T07:00:00Z",
    updated_at: "2026-11-14T09:00:00Z",
    ...overrides,
  };
}

function notification(index: number, overrides: Partial<Notification> = {}): Notification {
  return {
    id: `n-${index}`,
    event_type: "note",
    summary: `Heads up ${index}`,
    context_url: null,
    read_at: null,
    created_at: "2026-11-14T08:00:00Z",
    ...overrides,
  };
}

function task(index: number, overrides: Partial<ConversationTask> = {}): ConversationTask {
  return {
    id: `task-${index}`,
    source_conversation_id: `c-${index}`,
    conversation_id: `c-${index}`,
    agent_id: "a1",
    title: `Deep research ${index}`,
    status: "running",
    delivery: "pending",
    run_id: null,
    return_run_id: null,
    outcome: null,
    created_at: "2026-11-14T08:00:00Z",
    ...overrides,
  };
}

function renderWidget() {
  render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <TooltipProvider>
        <WaitingOnMeWidget
          title="Waiting on you"
          hint="Everything waiting on you"
          period={PERIOD}
          seeAll="/inbox"
        />
      </TooltipProvider>
    </NextIntlClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  usePendingMock.mockReturnValue({
    tools: [],
    steps: [],
    count: 0,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  });
  useThreadsMock.mockReturnValue({
    threads: [],
    total: 0,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  });
  useInboxMock.mockReturnValue({
    notifications: [],
    isLoading: false,
    error: null,
    refetch: vi.fn(),
    markRead: vi.fn(),
  });
  useTasksMock.mockReturnValue({
    data: [],
    isPending: false,
    error: null,
    refetch: vi.fn(),
    stop: { isPending: false, mutate: vi.fn() },
  });
});

describe("WaitingOnMeWidget", () => {
  it("says nothing is waiting when all four sources are empty", () => {
    renderWidget();

    expect(screen.getByText("Nothing waiting on you")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Approvals" })).toBeNull();
  });

  it("fans the four sources into one list, unread and active only", () => {
    usePendingMock.mockReturnValue({
      tools: [toolApproval()],
      steps: [stepApproval()],
      count: 2,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    useThreadsMock.mockReturnValue({
      threads: [thread(1), thread(2, { unread: false })],
      total: 2,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    useInboxMock.mockReturnValue({
      notifications: [notification(1), notification(2, { read_at: "2026-11-14T09:00:00Z" })],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      markRead: vi.fn(),
    });
    useTasksMock.mockReturnValue({
      data: [task(1), task(2, { status: "completed" })],
      isPending: false,
      error: null,
      refetch: vi.fn(),
      stop: { isPending: false, mutate: vi.fn() },
    });
    renderWidget();

    const approvals = screen.getByRole("region", { name: "Approvals" });
    const [toolRow, stepRow] = within(approvals).getAllByRole("listitem");
    expect(toolRow).toHaveTextContent("Architect · Save to memory");
    expect(stepRow).toHaveTextContent("Send the draft to the team?");
    const [toolReview, stepReview] = within(approvals).getAllByRole("link", {
      name: "Review",
    });
    expect(toolReview).toHaveAttribute("href", "/runs?tab=approvals");
    expect(stepReview).toHaveAttribute("href", "/runs?tab=approvals");

    const messages = screen.getByRole("region", { name: "Agent messages" });
    expect(within(messages).getByText(/First 1/)).toBeInTheDocument();
    expect(within(messages).queryByText(/First 2/)).toBeNull();

    const notes = screen.getByRole("region", { name: "Notifications" });
    expect(within(notes).getByText("Heads up 1")).toBeInTheDocument();
    expect(within(notes).queryByText("Heads up 2")).toBeNull();

    const tasks = screen.getByRole("region", { name: "Background tasks" });
    expect(within(tasks).getByText("Deep research 1")).toBeInTheDocument();
    expect(within(tasks).queryByText("Deep research 2")).toBeNull();

    expect(screen.queryByText("Nothing waiting on you")).toBeNull();
  });

  it("caps each group at three rows and says how many more wait", () => {
    usePendingMock.mockReturnValue({
      tools: [
        toolApproval({ id: "ap1" }),
        toolApproval({ id: "ap2", agent_name: "Second" }),
        toolApproval({ id: "ap3", agent_name: "Third" }),
        toolApproval({ id: "ap4", agent_name: "Fourth" }),
        toolApproval({ id: "ap5", agent_name: "Fifth" }),
      ],
      steps: [],
      count: 5,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    renderWidget();

    const approvals = screen.getByRole("region", { name: "Approvals" });
    expect(within(approvals).getAllByRole("listitem")).toHaveLength(3);
    expect(within(approvals).getByText("2 more waiting")).toBeInTheDocument();
    expect(within(approvals).queryByText(/Fourth/)).toBeNull();
  });

  it("shows a skeleton while everything is still loading", () => {
    usePendingMock.mockReturnValue({
      tools: [],
      steps: [],
      count: 0,
      isLoading: true,
      error: null,
      refetch: vi.fn(),
    });
    useThreadsMock.mockReturnValue({
      threads: [],
      total: 0,
      isLoading: true,
      error: null,
      refetch: vi.fn(),
    });
    useInboxMock.mockReturnValue({
      notifications: [],
      isLoading: true,
      error: null,
      refetch: vi.fn(),
      markRead: vi.fn(),
    });
    useTasksMock.mockReturnValue({
      data: undefined,
      isPending: true,
      error: null,
      refetch: vi.fn(),
      stop: { isPending: false, mutate: vi.fn() },
    });
    renderWidget();

    expect(screen.queryByText("Nothing waiting on you")).toBeNull();
    expect(screen.queryByRole("region", { name: "Approvals" })).toBeNull();
  });

  it("offers one retry when every source failed", async () => {
    const refetches = [vi.fn(), vi.fn(), vi.fn(), vi.fn()];
    usePendingMock.mockReturnValue({
      tools: [],
      steps: [],
      count: 0,
      isLoading: false,
      error: new Error("down"),
      refetch: refetches[0],
    });
    useThreadsMock.mockReturnValue({
      threads: [],
      total: 0,
      isLoading: false,
      error: "down",
      refetch: refetches[1],
    });
    useInboxMock.mockReturnValue({
      notifications: [],
      isLoading: false,
      error: "down",
      refetch: refetches[2],
      markRead: vi.fn(),
    });
    useTasksMock.mockReturnValue({
      data: undefined,
      isPending: false,
      error: new Error("down"),
      refetch: refetches[3],
      stop: { isPending: false, mutate: vi.fn() },
    });
    renderWidget();

    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    for (const refetch of refetches) expect(refetch).toHaveBeenCalled();
  });

  it("keeps the groups that loaded when one source failed, with its own retry", async () => {
    const threadsRefetch = vi.fn();
    useThreadsMock.mockReturnValue({
      threads: [],
      total: 0,
      isLoading: false,
      error: "down",
      refetch: threadsRefetch,
    });
    useInboxMock.mockReturnValue({
      notifications: [notification(1)],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
      markRead: vi.fn(),
    });
    renderWidget();

    const messages = screen.getByRole("region", { name: "Agent messages" });
    expect(within(messages).getByText("Couldn't load these right now.")).toBeInTheDocument();
    await userEvent.click(within(messages).getByRole("button", { name: "Try again" }));
    expect(threadsRefetch).toHaveBeenCalled();
    expect(screen.getByRole("region", { name: "Notifications" })).toBeInTheDocument();
  });
});
